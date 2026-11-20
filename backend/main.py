import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import paho.mqtt.client as mqtt
from web3 import Web3

import database as db
from blockchain import BlockchainClient
from ipfs_client import upload_json_to_ipfs
from analytics import evaluate_realtime_thresholds, run_analytics_report, CO2_KG_PER_KWH, BUILDING_NAMES

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = "energy/readings/#"

bc: BlockchainClient | None = None
mqtt_client: mqtt.Client | None = None


def handle_mqtt_message(_client, _userdata, msg):
    try:
        envelope = json.loads(msg.payload.decode("utf-8"))
        raw_payload = envelope["raw_payload"]
        signature = envelope["signature"]
        payload = json.loads(raw_payload)

        device_addr = payload["device_address"]
        energy_wh = int(payload["energy_wh"])
        power_w = int(payload["power_w"])
        timestamp = int(payload["timestamp"])

        cid_hash_bytes = Web3.keccak(text=raw_payload)
        cid_hash_hex = "0x" + cid_hash_bytes.hex().removeprefix("0x")

        if not bc.verify_reading_signature(
            device_addr, energy_wh, power_w, timestamp, cid_hash_bytes, signature
        ):
            print(f"[REJECTED] Invalid signature from {device_addr}")
            return

        # 1. Off-chain загрузка в IPFS
        ipfs_cid = upload_json_to_ipfs(raw_payload)

        # 2. On-chain фиксация
        receipt = bc.submit_reading(
            device_addr, energy_wh, power_w, timestamp, cid_hash_bytes, signature
        )

        # 3. Real-time проверка пороговых аномалий (>150% пики, >5% утечки)
        dev_info = db.fetch_device(device_addr)
        recent = db.fetch_device_readings(device_addr)
        if dev_info:
            alerts = evaluate_realtime_thresholds(
                energy_wh=energy_wh,
                power_w=power_w,
                baseline_wh=int(dev_info["baseline_wh"]),
                recent_readings=recent,
            )
            for al in alerts:
                db.insert_anomaly(
                    device_addr=device_addr,
                    anomaly_type=al["anomaly_type"],
                    severity=al["severity"],
                    metric_value=al["metric_value"],
                    threshold_value=al["threshold_value"],
                    details=al["details"],
                    reading_timestamp=timestamp,
                )

        # 4. Индексация в PostgreSQL
        db.insert_reading(
            {
                "device_address": device_addr,
                "energy_wh": energy_wh,
                "power_w": power_w,
                "timestamp": timestamp,
                "ipfs_cid": ipfs_cid,
                "ipfs_cid_hash": cid_hash_hex,
                "tx_hash": receipt["tx_hash"],
                "gas_used": receipt["gas_used"],
                "block_number": receipt["block_number"],
            }
        )
        print(
            f"[RECORDED] {device_addr[:10]}... energy={energy_wh}Wh power={power_w}W "
            f"gas={receipt['gas_used']} ipfs={ipfs_cid[:12]}..."
        )
    except Exception as exc:
        print(f"[ERROR] Failed to process MQTT reading: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global bc, mqtt_client
    db.init_db()
    bc = BlockchainClient()

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_message = handle_mqtt_message
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    mqtt_client.subscribe(MQTT_TOPIC)
    mqtt_client.loop_start()
    print(f"Subscribed to MQTT {MQTT_HOST}:{MQTT_PORT} topic '{MQTT_TOPIC}'")

    yield

    mqtt_client.loop_stop()
    mqtt_client.disconnect()


app = FastAPI(title="Urban Energy Web3 Audit API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DeviceRegisterRequest(BaseModel):
    device_address: str
    consumer_address: str
    building_type: int = 0
    baseline_wh: int = 10000
    label: str = ""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "blockchain_connected": bc.w3.is_connected(),
        "chain_id": bc.chain_id,
        "contracts": bc.deployment,
        "readings_count": db.count_readings(),
    }


@app.post("/api/devices/register")
def register_device(req: DeviceRegisterRequest):
    try:
        tx_hash = bc.register_device(
            req.device_address, req.consumer_address, req.building_type, req.baseline_wh
        )
        db.upsert_device(
            req.device_address,
            req.consumer_address,
            req.building_type,
            req.baseline_wh,
            tx_hash,
            req.label,
        )
        return {"status": "registered", "device_address": req.device_address, "tx_hash": tx_hash}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/devices")
def list_devices():
    devices = db.fetch_devices()
    result = []
    for d in devices:
        rewards = bc.get_consumer_rewards(d["consumer_address"])
        readings = db.fetch_device_readings(d["device_address"])
        total_wh = sum(int(r["energy_wh"]) for r in readings)
        result.append(
            {
                **d,
                "building_name": BUILDING_NAMES.get(int(d["building_type"]), "Другое"),
                "readings_count": len(readings),
                "total_wh": total_wh,
                "total_kwh": round(total_wh / 1000.0, 3),
                "co2_kg": round((total_wh / 1000.0) * CO2_KG_PER_KWH, 3),
                **rewards,
            }
        )
    return result


@app.get("/api/readings")
def get_readings(limit: int = 50):
    return db.fetch_recent_readings(limit)


@app.get("/api/anomalies")
def get_anomalies(limit: int = 100):
    return db.fetch_anomalies(limit)


@app.get("/api/analytics/overview")
def analytics_overview():
    readings = db.fetch_all_readings()
    devices = db.fetch_devices()
    report = run_analytics_report(readings, devices)
    report["realtime_alerts_count"] = len(db.fetch_anomalies(1000))
    return report


@app.get("/api/audit/{device_address}")
def audit_device(device_address: str):
    t0 = time.perf_counter()
    onchain = bc.get_onchain_readings(device_address)
    offchain = db.fetch_device_readings(device_address)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "device_address": device_address,
        "audit_latency_ms": elapsed_ms,
        "onchain_count": len(onchain),
        "offchain_count": len(offchain),
        "integrity_matched": len(onchain) == len(offchain),
        "onchain_readings": onchain,
        "offchain_index": offchain,
    }


def _settle_single_device(dev: dict) -> dict:
    readings = db.fetch_device_readings(dev["device_address"])
    if not readings:
        raise HTTPException(status_code=400, detail="No readings recorded for device")

    actual_wh = sum(int(r["energy_wh"]) for r in readings)
    baseline_wh = int(dev["baseline_wh"])
    savings_pct = round(((baseline_wh - actual_wh) / baseline_wh) * 100.0, 2)

    cert_uri = ""
    if savings_pct >= 10.0:
        cert_meta = json.dumps(
            {
                "name": f"Eco Certificate — {dev.get('label') or dev['device_address'][:10]}",
                "description": f"Verified energy savings of {savings_pct}% below baseline.",
                "device_address": dev["device_address"],
                "consumer_address": dev["consumer_address"],
                "baseline_wh": baseline_wh,
                "actual_wh": actual_wh,
                "savings_pct": savings_pct,
                "co2_saved_kg": round(((baseline_wh - actual_wh) / 1000.0) * CO2_KG_PER_KWH, 3),
            },
            ensure_ascii=False,
        )
        cid = upload_json_to_ipfs(cert_meta)
        cert_uri = f"ipfs://{cid}"

    res = bc.settle_period(dev["device_address"], actual_wh, cert_uri)
    record = {
        "device_address": dev["device_address"],
        "consumer_address": dev["consumer_address"],
        "baseline_wh": baseline_wh,
        "actual_wh": actual_wh,
        "savings_pct": savings_pct,
        "tokens_awarded": res["tokens_awarded"],
        "certificate_id": res["certificate_id"],
        "certificate_uri": cert_uri,
        "penalty_delta": res["penalty_delta"],
        "tx_hash": res["tx_hash"],
    }
    db.insert_settlement(record)
    return record


@app.post("/api/settle/{device_address}")
def settle_device(device_address: str):
    dev = db.fetch_device(device_address)
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    return _settle_single_device(dev)


@app.post("/api/settle-all")
def settle_all():
    devices = db.fetch_devices()
    results = []
    for dev in devices:
        readings = db.fetch_device_readings(dev["device_address"])
        if readings:
            results.append(_settle_single_device(dev))
    return {"settled_count": len(results), "results": results}


@app.get("/api/settlements")
def list_settlements(limit: int = 100):
    return db.fetch_settlements(limit)