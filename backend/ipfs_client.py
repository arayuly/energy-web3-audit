import os
import httpx

IPFS_API_URL = f"http://{os.getenv('IPFS_HOST', '127.0.0.1')}:{os.getenv('IPFS_API_PORT', '5001')}/api/v0"


def upload_json_to_ipfs(raw_json: str) -> str:
    resp = httpx.post(
        f"{IPFS_API_URL}/add",
        files={"file": ("reading.json", raw_json.encode("utf-8"))},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["Hash"]