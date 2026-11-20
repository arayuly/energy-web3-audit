import json
import os
import threading
from pathlib import Path
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

ROOT_DIR = Path(__file__).resolve().parent.parent
DEPLOYMENT_PATH = ROOT_DIR / "contracts" / "deployment.json"
ARTIFACTS_DIR = ROOT_DIR / "contracts" / "artifacts" / "contracts"

RPC_URL = os.getenv("ETH_RPC_URL", "http://127.0.0.1:8545")
ADMIN_PRIVATE_KEY = os.getenv(
    "ADMIN_PRIVATE_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
)


def _load_abi(sol_file: str, contract_name: str):
    path = ARTIFACTS_DIR / sol_file / f"{contract_name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["abi"]


class BlockchainClient:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.tx_lock = threading.Lock()
        self.account = Account.from_key(ADMIN_PRIVATE_KEY)

        with open(DEPLOYMENT_PATH, "r", encoding="utf-8") as f:
            self.deployment = json.load(f)

        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.deployment["energyAuditSystem"]),
            abi=_load_abi("EnergyAuditSystem.sol", "EnergyAuditSystem"),
        )
        self.eco_token = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.deployment["ecoToken"]),
            abi=_load_abi("EcoToken.sol", "EcoToken"),
        )
        self.eco_cert = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.deployment["ecoCertificate"]),
            abi=_load_abi("EcoCertificate.sol", "EcoCertificate"),
        )

    @property
    def chain_id(self) -> int:
        return self.w3.eth.chain_id

    def _send_tx(self, fn_call):
        with self.tx_lock:
            nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")
            tx = fn_call.build_transaction(
                {
                    "from": self.account.address,
                    "nonce": nonce,
                    "chainId": self.chain_id,
                    "gasPrice": self.w3.eth.gas_price,
                }
            )
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt["status"] != 1:
                raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
            return receipt

    def is_device_active(self, device_addr: str) -> bool:
        dev = self.contract.functions.devices(Web3.to_checksum_address(device_addr)).call()
        return bool(dev[4])

    def register_device(self, device_addr: str, consumer_addr: str, building_type: int, baseline_wh: int) -> str:
        if self.is_device_active(device_addr):
            return "already_registered"
        receipt = self._send_tx(
            self.contract.functions.registerDevice(
                Web3.to_checksum_address(device_addr),
                Web3.to_checksum_address(consumer_addr),
                int(building_type),
                int(baseline_wh),
            )
        )
        return "0x" + receipt["transactionHash"].hex().removeprefix("0x")

    def verify_reading_signature(
        self,
        device_addr: str,
        energy_wh: int,
        power_w: int,
        timestamp: int,
        cid_hash_bytes: bytes,
        signature_hex: str,
    ) -> bool:
        data_hash = Web3.solidity_keccak(
            ["address", "uint64", "uint32", "uint64", "bytes32", "uint256"],
            [
                Web3.to_checksum_address(device_addr),
                int(energy_wh),
                int(power_w),
                int(timestamp),
                cid_hash_bytes,
                self.chain_id,
            ],
        )
        msg = encode_defunct(primitive=data_hash)
        recovered = Account.recover_message(msg, signature=bytes.fromhex(signature_hex.removeprefix("0x")))
        return recovered.lower() == device_addr.lower()

    def submit_reading(
        self,
        device_addr: str,
        energy_wh: int,
        power_w: int,
        timestamp: int,
        cid_hash_bytes: bytes,
        signature_hex: str,
    ) -> dict:
        sig_bytes = bytes.fromhex(signature_hex.removeprefix("0x"))
        receipt = self._send_tx(
            self.contract.functions.submitReading(
                Web3.to_checksum_address(device_addr),
                int(energy_wh),
                int(power_w),
                int(timestamp),
                cid_hash_bytes,
                sig_bytes,
            )
        )
        return {
            "tx_hash": "0x" + receipt["transactionHash"].hex().removeprefix("0x"),
            "gas_used": int(receipt["gasUsed"]),
            "block_number": int(receipt["blockNumber"]),
        }

    def settle_period(self, device_addr: str, period_total_wh: int, certificate_uri: str = "") -> dict:
        receipt = self._send_tx(
            self.contract.functions.settlePeriod(
                Web3.to_checksum_address(device_addr),
                int(period_total_wh),
                certificate_uri,
            )
        )
        tx_hash = "0x" + receipt["transactionHash"].hex().removeprefix("0x")

        tokens_awarded = 0.0
        certificate_id = 0
        penalty_delta = 0

        bonus_logs = self.contract.events.BonusAwarded().process_receipt(receipt)
        if bonus_logs:
            args = bonus_logs[0]["args"]
            tokens_awarded = float(args["tokenAmount"]) / 1e18
            certificate_id = int(args["certificateId"])

        penalty_logs = self.contract.events.PenaltyRecorded().process_receipt(receipt)
        if penalty_logs:
            args = penalty_logs[0]["args"]
            penalty_delta = int(args["excessBps"]) // 100

        return {
            "tx_hash": tx_hash,
            "gas_used": int(receipt["gasUsed"]),
            "tokens_awarded": tokens_awarded,
            "certificate_id": certificate_id,
            "penalty_delta": penalty_delta,
        }

    def get_consumer_rewards(self, consumer_addr: str) -> dict:
        addr = Web3.to_checksum_address(consumer_addr)
        ecot_wei = self.eco_token.functions.balanceOf(addr).call()
        cert_count = self.eco_cert.functions.balanceOf(addr).call()
        penalty = self.contract.functions.penaltyScore(addr).call()
        return {
            "ecot_balance": round(float(ecot_wei) / 1e18, 2),
            "certificates_count": int(cert_count),
            "penalty_score": int(penalty),
        }

    def get_onchain_readings(self, device_addr: str) -> list[dict]:
        rows = self.contract.functions.getDeviceReadings(Web3.to_checksum_address(device_addr)).call()
        return [
            {
                "timestamp": int(r[0]),
                "energy_wh": int(r[1]),
                "power_w": int(r[2]),
                "ipfs_cid_hash": "0x" + r[3].hex().removeprefix("0x"),
            }
            for r in rows
        ]