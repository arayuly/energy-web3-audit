import json
import os
import random
import time
import httpx
import paho.mqtt.client as mqtt
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
CHAIN_ID = 31337
STEPS_PER_DEVICE = 6

# Модель городской микро-сети (20 потребителей: >=5 жилых, >=3 офисов, >=2 общественных + освещение)
MICROGRID_PROFILE = [
    # 10 жилых домов (building_type=0)
    ("Жилой дом №1", 0, 12000, "saving"),
    ("Жилой дом №2", 0, 12000, "saving"),
    ("Жилой дом №3", 0, 12000, "saving"),
    ("Жилой дом №4", 0, 12000, "saving"),
    ("Жилой дом №5", 0, 12000, "saving"),
    ("Жилой дом №6", 0, 12000, "saving"),
    ("Жилой дом №7", 0, 12000, "normal"),
    ("Жилой дом №8", 0, 12000, "normal"),
    ("Жилой дом №9", 0, 12000, "peak"),
    ("Жилой дом №10", 0, 12000, "leak"),
    # 4 офиса (building_type=1)
    ("Офисный центр А", 1, 24000, "saving"),
    ("Офисный центр Б", 1, 24000, "saving"),
    ("Офисный центр В", 1, 24000, "peak"),
    ("Офисный центр Г", 1, 24000, "leak"),
    # 4 общественных объекта (building_type=2)
    ("Школа №14", 2, 18000, "saving"),
    ("Поликлиника №3", 2, 18000, "saving"),
    ("Библиотека", 2, 18000, "normal"),
    ("Спорткомплекс", 2, 18000, "peak"),
    # 2 линии уличного освещения (building_type=3)
    ("Уличное освещение — Центр", 3, 15000, "saving"),
    ("Уличное освещение — Парк", 3, 15000, "leak"),
]


def sign_reading(priv_key: str, device_addr: str, energy_wh: int, power_w: int, ts: int, raw_payload: str) -> str:
    cid_hash = Web3.keccak(text=raw_payload)
    data_hash = Web3.solidity_keccak(
        ["address", "uint64", "uint32", "uint64", "bytes32", "uint256"],
        [Web3.to_checksum_address(device_addr), energy_wh, power_w, ts, cid_hash, CHAIN_ID],
    )
    signed = Account.sign_message(encode_defunct(primitive=data_hash), private_key=priv_key)
    return "0x" + signed.signature.hex().removeprefix("0x")


def main():
    random.seed(42)
    initial_count = httpx.get(f"{API_URL}/health", timeout=10.0).json()["readings_count"]

    devices = []
    print(f"1) Регистрация {len(MICROGRID_PROFILE)} виртуальных объектов микро-сети...")
    for label, btype, baseline_wh, scenario in MICROGRID_PROFILE:
        dev_acc = Account.create()
        consumer_acc = Account.create()
        resp = httpx.post(
            f"{API_URL}/api/devices/register",
            json={
                "device_address": dev_acc.address,
                "consumer_address": consumer_acc.address,
                "building_type": btype,
                "baseline_wh": baseline_wh,
                "label": label,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        devices.append(
            {
                "label": label,
                "btype": btype,
                "baseline_wh": baseline_wh,
                "scenario": scenario,
                "address": dev_acc.address,
                "priv_key": dev_acc.key.hex(),
                "consumer": consumer_acc.address,
            }
        )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    expected_anomalous_devices = set()
    total_baseline_wh = 0
    total_actual_wh = 0
    base_ts = int(time.time())

    print(f"2) Генерация телеметрии ({len(devices) * STEPS_PER_DEVICE} подписанных показаний по MQTT)...")
    try:
        for step in range(STEPS_PER_DEVICE):
            for dev in devices:
                step_base = dev["baseline_wh"] / STEPS_PER_DEVICE
                scenario = dev["scenario"]

                if scenario == "saving":
                    # Экономия 16-24% за счет токенизированных стимулов
                    factor = random.uniform(0.76, 0.84)
                    energy_wh = int(step_base * factor)
                    power_w = int(step_base * factor * random.uniform(0.95, 1.05))
                elif scenario == "normal":
                    factor = random.uniform(0.96, 1.02)
                    energy_wh = int(step_base * factor)
                    power_w = int(step_base * factor * random.uniform(0.95, 1.05))
                elif scenario == "peak":
                    if step in (3, 4):
                        # Искусственный пик нагрузки > 165% от среднего
                        energy_wh = int(step_base * random.uniform(1.65, 1.85))
                        power_w = int(step_base * random.uniform(1.75, 1.95))
                        expected_anomalous_devices.add(dev["address"].lower())
                    else:
                        energy_wh = int(step_base * random.uniform(0.95, 1.02))
                        power_w = int(step_base * random.uniform(0.95, 1.02))
                else:  # leak
                    # Фоновая утечка +12..18% выше базы на каждом шаге
                    factor = random.uniform(1.12, 1.18)
                    energy_wh = int(step_base * factor)
                    power_w = int(step_base * factor * random.uniform(0.98, 1.04))
                    expected_anomalous_devices.add(dev["address"].lower())

                ts = base_ts + step * 60 + 1
                payload = {
                    "device_address": dev["address"],
                    "building_type": dev["btype"],
                    "energy_wh": energy_wh,
                    "power_w": power_w,
                    "timestamp": ts,
                }
                raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                sig = sign_reading(dev["priv_key"], dev["address"], energy_wh, power_w, ts, raw_payload)

                client.publish(
                    f"energy/readings/{dev['address']}",
                    json.dumps({"raw_payload": raw_payload, "signature": sig}),
                )
                total_actual_wh += energy_wh
                time.sleep(0.03)

            time.sleep(0.2)

        for dev in devices:
            total_baseline_wh += dev["baseline_wh"]

        # Ждем, пока бэкенд запишет все показания в IPFS и блокчейн
        target_count = initial_count + len(devices) * STEPS_PER_DEVICE
        print("3) Ожидание финализации транзакций в блокчейне...")
        for _ in range(120):
            curr = httpx.get(f"{API_URL}/health", timeout=10.0).json()["readings_count"]
            if curr >= target_count:
                break
            time.sleep(0.5)
    finally:
        client.loop_stop()
        client.disconnect()

    print("4) Расчет бонусов (ERC-20 / ERC-721) и штрафов через смарт-контракт...")
    settle_resp = httpx.post(f"{API_URL}/api/settle-all", timeout=60.0).json()
    total_tokens = sum(r["tokens_awarded"] for r in settle_resp["results"])
    total_certs = sum(1 for r in settle_resp["results"] if r["certificate_id"] > 0)

    print("5) Проверка аналитики, точности детекции и скорости аудита...")
    overview = httpx.get(f"{API_URL}/api/analytics/overview", timeout=20.0).json()
    alerts = httpx.get(f"{API_URL}/api/anomalies?limit=500", timeout=20.0).json()

    detected_devices = {a["device_address"].lower() for a in alerts}
    sim_addresses = {d["address"].lower() for d in devices}
    tp = len(expected_anomalous_devices & detected_devices)
    tn = len((sim_addresses - expected_anomalous_devices) - (detected_devices & sim_addresses))
    accuracy_pct = round(((tp + tn) / len(sim_addresses)) * 100.0, 2)

    reduction_pct = round(((total_baseline_wh - total_actual_wh) / total_baseline_wh) * 100.0, 2)

    audit_check = httpx.get(f"{API_URL}/api/audit/{devices[0]['address']}", timeout=15.0).json()

    print("\n================ ИТОГИ ВАЛИДАЦИИ TRL 5 ================")
    print(f"Объектов в микро-сети:          {len(devices)}")
    print(f"Всего записей в блокчейне:      {overview['summary']['total_readings']}")
    print(f"Средний расход газа:            {overview['summary']['avg_gas_used']} gas (цель < 150000)")
    print(f"Суммарное потребление:          {overview['summary']['total_kwh']} kWh ({overview['summary']['total_co2_kg']} kg CO2)")
    print(f"Снижение потребления vs база:   {reduction_pct}% (цель 12-20%)")
    print(f"Точность детекции аномалий:     {accuracy_pct}% (цель >= 92%)")
    print(f"Выпущено токенов ECOT:          {round(total_tokens, 2)} ECOT")
    print(f"Выпущено NFT-сертификатов:      {total_certs} шт.")
    print(f"Время аудита 1 объекта:         {audit_check['audit_latency_ms']} мс (цель < 10000 мс)")
    print(f"Сверка On-chain == Off-chain:   {audit_check['integrity_matched']}")
    print("=======================================================")


if __name__ == "__main__":
    main()