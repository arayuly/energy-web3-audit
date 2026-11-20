import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DSN = (
    f"host={os.getenv('POSTGRES_HOST', '127.0.0.1')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')} "
    f"dbname={os.getenv('POSTGRES_DB', 'energy_audit')} "
    f"user={os.getenv('POSTGRES_USER', 'energy_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'energy_secret_pass')}"
)


def get_conn():
    return psycopg.connect(DSN, row_factory=dict_row)


def init_db():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_address TEXT PRIMARY KEY,
                consumer_address TEXT NOT NULL,
                building_type INT NOT NULL,
                baseline_wh BIGINT NOT NULL,
                label TEXT DEFAULT '',
                tx_hash TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            ALTER TABLE devices ADD COLUMN IF NOT EXISTS label TEXT DEFAULT '';

            CREATE TABLE IF NOT EXISTS readings (
                id SERIAL PRIMARY KEY,
                device_address TEXT NOT NULL,
                energy_wh BIGINT NOT NULL,
                power_w INT NOT NULL,
                reading_timestamp BIGINT NOT NULL,
                ipfs_cid TEXT NOT NULL,
                ipfs_cid_hash TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                gas_used BIGINT NOT NULL,
                block_number BIGINT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS anomalies (
                id SERIAL PRIMARY KEY,
                device_address TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                metric_value DOUBLE PRECISION NOT NULL,
                threshold_value DOUBLE PRECISION NOT NULL,
                details TEXT NOT NULL,
                reading_timestamp BIGINT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id SERIAL PRIMARY KEY,
                device_address TEXT NOT NULL,
                consumer_address TEXT NOT NULL,
                baseline_wh BIGINT NOT NULL,
                actual_wh BIGINT NOT NULL,
                savings_pct DOUBLE PRECISION NOT NULL,
                tokens_awarded DOUBLE PRECISION NOT NULL,
                certificate_id BIGINT DEFAULT 0,
                certificate_uri TEXT DEFAULT '',
                penalty_delta INT DEFAULT 0,
                tx_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        conn.commit()


def upsert_device(
    device_addr: str,
    consumer_addr: str,
    building_type: int,
    baseline_wh: int,
    tx_hash: str,
    label: str = "",
):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (device_address, consumer_address, building_type, baseline_wh, label, tx_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (device_address) DO UPDATE
            SET label = COALESCE(NULLIF(EXCLUDED.label, ''), devices.label)
            """,
            (device_addr, consumer_addr, building_type, baseline_wh, label, tx_hash),
        )
        conn.commit()


def fetch_devices():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM devices ORDER BY created_at ASC")
        return cur.fetchall()


def fetch_device(device_addr: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM devices WHERE LOWER(device_address) = LOWER(%s)",
            (device_addr,),
        )
        return cur.fetchone()


def insert_reading(data: dict):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO readings (
                device_address, energy_wh, power_w, reading_timestamp,
                ipfs_cid, ipfs_cid_hash, tx_hash, gas_used, block_number
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data["device_address"],
                data["energy_wh"],
                data["power_w"],
                data["timestamp"],
                data["ipfs_cid"],
                data["ipfs_cid_hash"],
                data["tx_hash"],
                data["gas_used"],
                data["block_number"],
            ),
        )
        conn.commit()


def fetch_recent_readings(limit: int = 50):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM readings ORDER BY id DESC LIMIT %s", (limit,))
        return cur.fetchall()


def fetch_all_readings():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.*, d.building_type, d.baseline_wh, d.consumer_address, d.label
            FROM readings r
            LEFT JOIN devices d ON LOWER(r.device_address) = LOWER(d.device_address)
            ORDER BY r.reading_timestamp ASC
            """
        )
        return cur.fetchall()


def fetch_device_readings(device_addr: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM readings WHERE LOWER(device_address) = LOWER(%s) ORDER BY reading_timestamp DESC",
            (device_addr,),
        )
        return cur.fetchall()


def count_readings() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM readings")
        return int(cur.fetchone()["cnt"])


def insert_anomaly(
    device_addr: str,
    anomaly_type: str,
    severity: str,
    metric_value: float,
    threshold_value: float,
    details: str,
    reading_timestamp: int,
):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO anomalies (
                device_address, anomaly_type, severity, metric_value,
                threshold_value, details, reading_timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                device_addr,
                anomaly_type,
                severity,
                float(metric_value),
                float(threshold_value),
                details,
                int(reading_timestamp),
            ),
        )
        conn.commit()


def fetch_anomalies(limit: int = 100):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.*, d.label, d.building_type
            FROM anomalies a
            LEFT JOIN devices d ON LOWER(a.device_address) = LOWER(d.device_address)
            ORDER BY a.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def insert_settlement(data: dict):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO settlements (
                device_address, consumer_address, baseline_wh, actual_wh,
                savings_pct, tokens_awarded, certificate_id, certificate_uri,
                penalty_delta, tx_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data["device_address"],
                data["consumer_address"],
                data["baseline_wh"],
                data["actual_wh"],
                data["savings_pct"],
                data["tokens_awarded"],
                data["certificate_id"],
                data["certificate_uri"],
                data["penalty_delta"],
                data["tx_hash"],
            ),
        )
        conn.commit()


def fetch_settlements(limit: int = 100):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM settlements ORDER BY id DESC LIMIT %s", (limit,))
        return cur.fetchall()