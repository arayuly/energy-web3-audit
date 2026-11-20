import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# Коэффициент выбросов для энергосистемы (кг CO2 на 1 кВт*ч)
CO2_KG_PER_KWH = 0.55

BUILDING_NAMES = {
    0: "Жилой дом",
    1: "Офис",
    2: "Общественное здание",
    3: "Уличное освещение",
}

# Номинальная базовая мощность (Вт) и среднее потребление за интервал (Вт*ч) по типам объектов
EXPECTED_STEP_COUNT = 6


def evaluate_realtime_thresholds(
    energy_wh: int,
    power_w: int,
    baseline_wh: int,
    recent_readings: list[dict],
) -> list[dict]:
    """
    Пороговая проверка показания в реальном времени:
    1) Пиковая нагрузка > 150% от среднего.
    2) Фоновая утечка / системный перерасход > 5% от базового шага.
    """
    alerts = []
    step_baseline = baseline_wh / EXPECTED_STEP_COUNT

    # Средняя мощность по истории устройства (или от базового уровня, если данных мало)
    if len(recent_readings) >= 3:
        avg_power = float(np.mean([r["power_w"] for r in recent_readings[:10]]))
    else:
        avg_power = step_baseline

    # Правило 1: пиковая нагрузка > 150% от среднего
    if avg_power > 0 and power_w > avg_power * 1.50:
        ratio_pct = round((power_w / avg_power) * 100.0, 1)
        alerts.append(
            {
                "anomaly_type": "PEAK_LOAD",
                "severity": "HIGH",
                "metric_value": float(power_w),
                "threshold_value": round(avg_power * 1.50, 1),
                "details": f"Пиковая нагрузка {power_w} W ({ratio_pct}% от среднего {round(avg_power)} W, порог >150%)",
            }
        )

    # Правило 2: утечка > 5% выше базового интервального уровня (при отсутствии экстремального пика)
    if step_baseline > 0 and energy_wh > step_baseline * 1.05 and energy_wh <= step_baseline * 1.50:
        excess_pct = round(((energy_wh - step_baseline) / step_baseline) * 100.0, 1)
        alerts.append(
            {
                "anomaly_type": "LEAK_SUSPECTED",
                "severity": "MEDIUM",
                "metric_value": float(energy_wh),
                "threshold_value": round(step_baseline * 1.05, 1),
                "details": f"Подозрение на утечку/перерасход: +{excess_pct}% к базе (порог >5%)",
            }
        )

    return alerts


def run_analytics_report(readings: list[dict], devices: list[dict]) -> dict:
    if not readings:
        return {
            "summary": {
                "total_readings": 0,
                "total_kwh": 0.0,
                "total_co2_kg": 0.0,
                "avg_gas_used": 0,
            },
            "by_building_type": [],
            "clusters": [],
            "ml_anomalies": [],
            "recommendations": [],
        }

    df = pd.DataFrame(readings)
    df["energy_wh"] = df["energy_wh"].astype(float)
    df["power_w"] = df["power_w"].astype(float)
    df["baseline_wh"] = df["baseline_wh"].fillna(10000).astype(float)
    df["building_type"] = df["building_type"].fillna(0).astype(int)

    total_kwh = round(float(df["energy_wh"].sum()) / 1000.0, 3)
    total_co2_kg = round(total_kwh * CO2_KG_PER_KWH, 3)
    avg_gas = int(round(float(df["gas_used"].mean())))

    # Разрез по типам зданий
    by_type = []
    for btype, grp in df.groupby("building_type"):
        kwh = round(float(grp["energy_wh"].sum()) / 1000.0, 3)
        by_type.append(
            {
                "building_type": int(btype),
                "name": BUILDING_NAMES.get(int(btype), "Другое"),
                "devices_count": int(grp["device_address"].nunique()),
                "total_kwh": kwh,
                "co2_kg": round(kwh * CO2_KG_PER_KWH, 3),
                "avg_power_w": round(float(grp["power_w"].mean()), 1),
            }
        )

    # Агрегация по устройствам для K-means кластеризации паттернов
    dev_df = (
        df.groupby("device_address")
        .agg(
            total_wh=("energy_wh", "sum"),
            avg_wh=("energy_wh", "mean"),
            max_power_w=("power_w", "max"),
            avg_power_w=("power_w", "mean"),
            baseline_wh=("baseline_wh", "first"),
            building_type=("building_type", "first"),
            label=("label", "first"),
            readings_count=("energy_wh", "count"),
        )
        .reset_index()
    )
    dev_df["utilization_ratio"] = dev_df["total_wh"] / dev_df["baseline_wh"].clip(lower=1.0)
    dev_df["peak_factor"] = dev_df["max_power_w"] / dev_df["avg_power_w"].clip(lower=1.0)

    n_clusters = min(3, len(dev_df))
    if n_clusters >= 2:
        scaler = StandardScaler()
        features = scaler.fit_transform(dev_df[["utilization_ratio", "peak_factor"]])
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        dev_df["cluster_id"] = kmeans.fit_predict(features)

        # Сортируем кластеры от самого экономичного к самому неэффективному
        cluster_order = (
            dev_df.groupby("cluster_id")["utilization_ratio"].mean().sort_values().index.tolist()
        )
        cluster_names = ["Энергоэффективный", "Нормальный профиль", "Неэффективный / Аномальный"]
        name_map = {
            cid: cluster_names[min(idx, len(cluster_names) - 1)]
            for idx, cid in enumerate(cluster_order)
        }
        dev_df["cluster_name"] = dev_df["cluster_id"].map(name_map)
    else:
        dev_df["cluster_id"] = 0
        dev_df["cluster_name"] = "Нормальный профиль"

    # ML-детекция аномалий на уровне отдельных показаний (Isolation Forest)
    ml_anomalies = []
    if len(df) >= 10:
        df["step_baseline"] = df["baseline_wh"] / EXPECTED_STEP_COUNT
        df["energy_ratio"] = df["energy_wh"] / df["step_baseline"].clip(lower=1.0)
        df["power_ratio"] = df["power_w"] / df["step_baseline"].clip(lower=1.0)

        iso = IsolationForest(contamination=0.12, random_state=42)
        preds = iso.fit_predict(df[["energy_ratio", "power_ratio"]])
        scores = iso.decision_function(df[["energy_ratio", "power_ratio"]])
        df["ml_anomaly"] = preds == -1
        df["anomaly_score"] = scores

        anomalous_rows = df[df["ml_anomaly"]].sort_values("anomaly_score").head(50)
        for _, row in anomalous_rows.iterrows():
            ml_anomalies.append(
                {
                    "device_address": str(row["device_address"]),
                    "label": str(row["label"] or ""),
                    "energy_wh": int(row["energy_wh"]),
                    "power_w": int(row["power_w"]),
                    "energy_ratio": round(float(row["energy_ratio"]), 2),
                    "power_ratio": round(float(row["power_ratio"]), 2),
                    "anomaly_score": round(float(row["anomaly_score"]), 4),
                    "timestamp": int(row["reading_timestamp"]),
                }
            )

    # Кластеры и рекомендации по каждому устройству
    clusters = []
    recommendations = []
    for _, row in dev_df.iterrows():
        util = float(row["utilization_ratio"])
        peak = float(row["peak_factor"])
        dev_kwh = round(float(row["total_wh"]) / 1000.0, 3)

        if peak > 1.45:
            rec = "Обнаружены резкие пиковые нагрузки (>150%). Перенесите энергоемкие циклы на ночной тариф и проверьте пусковые токи."
        elif util > 1.05:
            rec = "Фоновый перерасход >5% от базового уровня. Проверьте контур на утечки, режим ожидания оборудования и теплопотери."
        elif util <= 0.90:
            rec = "Отличная энергоэффективность (экономия >=10%). Объект претендует на бонусные ECOT-токены и NFT-сертификат."
        else:
            rec = "Потребление в норме. Снижение нагрузки на 10% откроет автоматическое начисление токенов ECOT."

        clusters.append(
            {
                "device_address": str(row["device_address"]),
                "label": str(row["label"] or ""),
                "building_type": int(row["building_type"]),
                "building_name": BUILDING_NAMES.get(int(row["building_type"]), "Другое"),
                "cluster_id": int(row["cluster_id"]),
                "cluster_name": str(row["cluster_name"]),
                "total_kwh": dev_kwh,
                "baseline_kwh": round(float(row["baseline_wh"]) / 1000.0, 3),
                "utilization_pct": round(util * 100.0, 1),
                "peak_factor": round(peak, 2),
                "co2_kg": round(dev_kwh * CO2_KG_PER_KWH, 3),
            }
        )
        recommendations.append(
            {
                "device_address": str(row["device_address"]),
                "label": str(row["label"] or ""),
                "cluster_name": str(row["cluster_name"]),
                "recommendation": rec,
            }
        )

    return {
        "summary": {
            "total_readings": int(len(df)),
            "total_devices": int(len(devices)),
            "total_kwh": total_kwh,
            "total_co2_kg": total_co2_kg,
            "avg_gas_used": avg_gas,
        },
        "by_building_type": by_type,
        "clusters": clusters,
        "ml_anomalies": ml_anomalies,
        "recommendations": recommendations,
    }