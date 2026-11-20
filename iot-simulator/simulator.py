import os
import json
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

# Account #1 — потребитель (жилой дом), Account #2 — умный счетчик (Hardhat default accounts)
CONSUMER_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
DEVICE_PRIVATE_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
device_account = Account.from_key(DEVICE_PRIVATE_KEY)


def sign_reading(device_addr: str, energy_wh: int, power_w: int, timestamp: int, raw_payload: str) -> str:
    cid_hash = Web3.keccak(text=raw_payload)
    data_hash = Web3.solidity_keccak(
        ["address", "uint64", "uint32", "uint64", "bytes32", "uint256"],
        [
            Web3.to_checksum_address(device_addr),
            energy_wh,
            power_w,
            timestamp,
            cid_hash,
            CHAIN_ID,
        ],
    )
    signed = Account.sign_message(encode_defunct(primitive=data_hash), private_key=DEVICE_PRIVATE_KEY)
    return "0x" + signed.signature.hex().removeprefix("0x")


def main():
    print(f"Registering smart meter {device_account.address}...")
    resp = httpx.post(
        f"{API_URL}/api/devices/register",
        json={
            "device_address": device_account.address,
            "consumer_address": CONSUMER_ADDRESS,
            "building_type": 0,
            "baseline_wh": 10000,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    print("Registration response:", resp.json())

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    last_ts = 0
    try:
        for i in range(5):
            ts = max(int(time.time()), last_ts + 1)
            last_ts = ts
            power_w = random.randint(1500, 3200)
            energy_wh = random.randint(800, 1600)

            payload = {
                "device_address": device_account.address,
                "building_type": "residential",
                "energy_wh": energy_wh,
                "power_w": power_w,
                "timestamp": ts,
            }
            raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            signature = sign_reading(device_account.address, energy_wh, power_w, ts, raw_payload)

            envelope = {"raw_payload": raw_payload, "signature": signature}
            topic = f"energy/readings/{device_account.address}"
            client.publish(topic, json.dumps(envelope))
            print(f"[{i+1}/5] Published signed reading: {energy_wh} Wh, {power_w} W")
            time.sleep(3)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()