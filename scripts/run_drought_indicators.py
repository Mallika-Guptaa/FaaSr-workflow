#!/usr/bin/env python3
"""Compute soil-moisture drought indicators from Action 7 VWC + SSURGO.

Important:
- The workflow intentionally uses the Action 7 bias-corrected VWC dataset.
- Temporal smoothing is not used.
- Layer-level FAW/stress can be computed immediately.
- Root-zone CAW/TAW/Dr and the operational root-zone alert are only produced
  when ROOT_ZONE_DEPTH_IN is explicitly supplied. No crop rooting depth is
  guessed by this script.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any

import boto3
import numpy as np
import pandas as pd
from botocore.exceptions import ClientError

BUCKET = os.getenv("SOIL_BUCKET", "faasr-bucket-smarttap-private")
REGION = os.getenv("SOIL_BUCKET_REGION", "us-east-1")

ACTION7_KEY = os.getenv(
    "ACTION7_KEY",
    "soil_sensor_processing/bias_correction/soil_moisture_bias_corrected.csv",
)
SSURGO_KEY = os.getenv(
    "SSURGO_KEY",
    "soil_sensor_processing/ssurgo/ssurgo_site_depth_hydraulic_properties.csv",
)
OUTPUT_PREFIX = os.getenv(
    "DROUGHT_OUTPUT_PREFIX",
    "soil_sensor_processing/drought_alerts",
).rstrip("/")
LOCAL_ARTIFACT_DIR = os.getenv(
    "LOCAL_ARTIFACT_DIR",
    "artifacts/drought",
)

VWC_COLUMN = "vwc_bias_corrected_cm3_cm3"


def s3_client():
    return boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id=os.environ["S3PRIVATE_AccessKey"],
        aws_secret_access_key=os.environ["S3PRIVATE_SecretKey"],
    )


def read_s3_bytes(key: str) -> bytes:
    try:
        return s3_client().get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except ClientError as exc:
        raise RuntimeError(f"Could not read s3://{BUCKET}/{key}: {exc}") from exc


def write_s3_bytes(key: str, data: bytes, content_type: str) -> None:
    s3_client().put_object(
        Bucket=BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def classify_faw(value: Any) -> tuple[str | None, str | None]:
    """Apply the supervisor-provided FAW drought categories."""
    if pd.isna(value):
        return None, None
    faw = float(value)
    if faw > 0.80:
        return "No Stress", "Green"
    if faw >= 0.60:
        return "Watch", "Yellow"
    if faw >= 0.40:
        return "Moderate", "Orange"
    if faw >= 0.20:
        return "Severe", "Red"
    return "Extreme", "Dark Red"


def parse_root_zone_depth() -> float | None:
    raw = os.getenv("ROOT_ZONE_DEPTH_IN", "").strip()
    if not raw:
        return None
    value = float(raw)
    if value <= 0:
        raise ValueError("ROOT_ZONE_DEPTH_IN must be greater than zero.")
    return value


def add_layer_metrics(sensor: pd.DataFrame, props: pd.DataFrame) -> pd.DataFrame:
    required_sensor = {"site_id", "date", "depth_in", VWC_COLUMN}
    missing = sorted(required_sensor - set(sensor.columns))
    if missing:
        raise RuntimeError(f"Action 7 dataset is missing required columns: {missing}")

    required_props = {
        "site_id",
        "sensor_depth_in",
        "SAT",
        "FC",
        "WP",
        "hydraulic_valid",
    }
    missing = sorted(required_props - set(props.columns))
    if missing:
        raise RuntimeError(f"SSURGO properties are missing required columns: {missing}")

    sensor = sensor.copy()
    sensor["date"] = pd.to_datetime(sensor["date"], errors="coerce")
    sensor["site_id"] = pd.to_numeric(sensor["site_id"], errors="coerce").astype("Int64")
    sensor["depth_in"] = pd.to_numeric(sensor["depth_in"], errors="coerce")
    sensor[VWC_COLUMN] = pd.to_numeric(sensor[VWC_COLUMN], errors="coerce")

    props = props.copy()
    props["site_id"] = pd.to_numeric(props["site_id"], errors="coerce").astype("Int64")
    props["sensor_depth_in"] = pd.to_numeric(props["sensor_depth_in"], errors="coerce")
    for col in ("SAT", "FC", "WP"):
        props[col] = pd.to_numeric(props[col], errors="coerce")

    merged = sensor.merge(
        props,
        how="left",
        left_on=["site_id", "depth_in"],
        right_on=["site_id", "sensor_depth_in"],
        validate="many_to_one",
        suffixes=("", "_ssurgo"),
    )

    theta = merged[VWC_COLUMN]
    denom = merged["FC"] - merged["WP"]
    raw_faw = (theta - merged["WP"]) / denom

    valid_hydraulic = (
        merged["SAT"].notna()
        & merged["FC"].notna()
        & merged["WP"].notna()
        & (merged["WP"] < merged["FC"])
        & (merged["FC"] < merged["SAT"])
        & (denom > 0)
    )

    merged["hydraulic_properties_available"] = valid_hydraulic
    merged["FAW_layer_raw"] = raw_faw.where(valid_hydraulic)
    merged["FAW_layer"] = raw_faw.clip(lower=0.0, upper=1.0).where(valid_hydraulic)

    merged["water_above_wp_vwc"] = (theta - merged["WP"]).clip(lower=0.0).where(valid_hydraulic)
    merged["water_deficit_to_fc_vwc"] = (merged["FC"] - theta).clip(lower=0.0).where(valid_hydraulic)
    merged["relative_saturation"] = (theta / merged["SAT"]).where(valid_hydraulic & (merged["SAT"] > 0))

    merged["vwc_below_wp"] = valid_hydraulic & (theta < merged["WP"])
    merged["vwc_above_fc"] = valid_hydraulic & (theta > merged["FC"])
    merged["vwc_above_sat"] = valid_hydraulic & (theta > merged["SAT"])

    labels = merged["FAW_layer"].apply(classify_faw)
    merged["layer_stress_category"] = [item[0] for item in labels]
    merged["layer_alert_color"] = [item[1] for item in labels]

    merged["layer_alert_is_diagnostic"] = True
    merged["layer_alert_note"] = np.where(
        valid_hydraulic,
        "Diagnostic layer-level FAW class; operational alert should use root-zone FAW.",
        "No layer FAW: SSURGO hydraulic properties unavailable for this depth.",
    )
    return merged


def layer_intervals(depths: list[float], root_depth_in: float) -> dict[float, tuple[float, float]]:
    """Assign representative intervals to sensor depths using midpoint boundaries."""
    selected = sorted({float(d) for d in depths if d <= root_depth_in})
    if not selected:
        return {}

    boundaries = [0.0]
    for left, right in zip(selected[:-1], selected[1:]):
        boundaries.append((left + right) / 2.0)
    boundaries.append(root_depth_in)

    # If the root depth is shallower than the last midpoint, trim safely.
    boundaries = [min(max(b, 0.0), root_depth_in) for b in boundaries]
    intervals: dict[float, tuple[float, float]] = {}
    for i, depth in enumerate(selected):
        top = boundaries[i]
        bottom = boundaries[i + 1]
        if bottom > top:
            intervals[depth] = (top, bottom)
    return intervals


def root_zone_metrics(layer_df: pd.DataFrame, root_depth_in: float) -> pd.DataFrame:
    """
    Calculate root-zone CAW, TAW, Dr and FAW by site/date.

    Sensor values represent midpoint-defined depth intervals. This assumption is
    explicitly exported so it can be reviewed when crop/root information arrives.
    """
    rows: list[dict[str, Any]] = []

    for site_id, site_df in layer_df.groupby("site_id"):
        site_depths = sorted(
            pd.to_numeric(site_df["depth_in"], errors="coerce")
            .dropna()
            .astype(float)
            .unique()
            .tolist()
        )
        intervals = layer_intervals(site_depths, root_depth_in)
        if not intervals:
            continue

        expected_thickness_in = sum(bottom - top for top, bottom in intervals.values())

        for date, daily in site_df.groupby("date"):
            taw_mm = 0.0
            caw_mm = 0.0
            sat_storage_mm = 0.0
            covered_in = 0.0
            layer_count = 0
            invalid_layers: list[float] = []

            for depth, (top, bottom) in intervals.items():
                match = daily[np.isclose(daily["depth_in"].astype(float), depth)]
                if match.empty:
                    invalid_layers.append(depth)
                    continue
                row = match.iloc[0]
                thickness_in = bottom - top
                thickness_mm = thickness_in * 25.4

                if not bool(row["hydraulic_properties_available"]) or pd.isna(row[VWC_COLUMN]):
                    invalid_layers.append(depth)
                    continue

                wp = float(row["WP"])
                fc = float(row["FC"])
                sat = float(row["SAT"])
                theta = float(row[VWC_COLUMN])

                taw_layer = max(fc - wp, 0.0) * thickness_mm
                # Plant-available water is bounded to [WP, FC].
                theta_for_available = min(max(theta, wp), fc)
                caw_layer = max(theta_for_available - wp, 0.0) * thickness_mm

                taw_mm += taw_layer
                caw_mm += caw_layer
                sat_storage_mm += sat * thickness_mm
                covered_in += thickness_in
                layer_count += 1

            coverage = (
                covered_in / expected_thickness_in
                if expected_thickness_in > 0
                else np.nan
            )
            complete = bool(np.isclose(coverage, 1.0, atol=1e-9))
            root_faw = caw_mm / taw_mm if complete and taw_mm > 0 else np.nan
            depletion_mm = taw_mm - caw_mm if complete else np.nan
            category, color = classify_faw(root_faw)

            rows.append({
                "site_id": int(site_id),
                "date": date,
                "root_zone_depth_in": root_depth_in,
                "root_zone_depth_mm": root_depth_in * 25.4,
                "root_zone_method": "sensor midpoint intervals clipped to configured root-zone depth",
                "root_zone_expected_thickness_in": expected_thickness_in,
                "root_zone_hydraulic_coverage_fraction": coverage,
                "root_zone_complete": complete,
                "root_zone_layers_used": layer_count,
                "root_zone_invalid_or_missing_depths_in": ",".join(str(x) for x in invalid_layers),
                "TAW_mm": taw_mm if complete else np.nan,
                "CAW_mm": caw_mm if complete else np.nan,
                "Dr_mm": depletion_mm,
                "FAW_rootzone": root_faw,
                "root_zone_stress_category": category,
                "root_zone_alert_color": color,
                "root_zone_saturated_storage_mm": sat_storage_mm if complete else np.nan,
            })

    return pd.DataFrame(rows)


def main() -> None:
    sensor = pd.read_csv(io.BytesIO(read_s3_bytes(ACTION7_KEY)))
    props = pd.read_csv(io.BytesIO(read_s3_bytes(SSURGO_KEY)))
    root_depth_in = parse_root_zone_depth()

    layer = add_layer_metrics(sensor, props)
    root = (
        root_zone_metrics(layer, root_depth_in)
        if root_depth_in is not None
        else pd.DataFrame()
    )

    layer_valid = int(layer["FAW_layer"].notna().sum())
    layer_missing = int(layer["FAW_layer"].isna().sum())

    manifest: dict[str, Any] = {
        "status": "complete_layer_metrics",
        "input_vwc_dataset": f"s3://{BUCKET}/{ACTION7_KEY}",
        "input_ssurgo_dataset": f"s3://{BUCKET}/{SSURGO_KEY}",
        "smoothing_used": False,
        "vwc_column": VWC_COLUMN,
        "layer_rows": int(len(layer)),
        "layer_faw_rows": layer_valid,
        "layer_rows_without_hydraulic_metrics": layer_missing,
        "faw_thresholds": {
            ">0.80": "No Stress / Green",
            "0.60-0.80": "Watch / Yellow",
            "0.40-0.60": "Moderate / Orange",
            "0.20-0.40": "Severe / Red",
            "<0.20": "Extreme / Dark Red",
        },
        "root_zone_depth_in": root_depth_in,
        "root_zone_metrics_status": (
            "calculated" if root_depth_in is not None else "pending_crop_rooting_depth"
        ),
        "root_zone_note": (
            "No rooting depth was assumed. Set ROOT_ZONE_DEPTH_IN after crop/rooting-depth "
            "information is approved."
            if root_depth_in is None
            else "Root-zone metrics use midpoint-defined sensor intervals; review this layer-representation assumption."
        ),
    }

    if not root.empty:
        manifest.update({
            "root_zone_rows": int(len(root)),
            "root_zone_complete_rows": int(root["root_zone_complete"].sum()),
            "root_zone_incomplete_rows": int((~root["root_zone_complete"]).sum()),
        })

    os.makedirs(LOCAL_ARTIFACT_DIR, exist_ok=True)

    layer_bytes = layer.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8")
    manifest_bytes = json.dumps(manifest, indent=2, default=str).encode("utf-8")

    local_layer = os.path.join(LOCAL_ARTIFACT_DIR, "soil_moisture_layer_drought_indicators.csv")
    local_manifest = os.path.join(LOCAL_ARTIFACT_DIR, "drought_indicator_manifest.json")
    with open(local_layer, "wb") as handle:
        handle.write(layer_bytes)
    with open(local_manifest, "wb") as handle:
        handle.write(manifest_bytes)

    write_s3_bytes(
        f"{OUTPUT_PREFIX}/soil_moisture_layer_drought_indicators.csv",
        layer_bytes,
        "text/csv",
    )
    write_s3_bytes(
        f"{OUTPUT_PREFIX}/drought_indicator_manifest.json",
        manifest_bytes,
        "application/json",
    )

    if not root.empty:
        root_bytes = root.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8")
        local_root = os.path.join(LOCAL_ARTIFACT_DIR, "soil_moisture_root_zone_drought_indicators.csv")
        with open(local_root, "wb") as handle:
            handle.write(root_bytes)
        write_s3_bytes(
            f"{OUTPUT_PREFIX}/soil_moisture_root_zone_drought_indicators.csv",
            root_bytes,
            "text/csv",
        )

    print(json.dumps(manifest, indent=2, default=str))

    print("\nLayer stress counts (valid hydraulic rows only):")
    counts = (
        layer["layer_stress_category"]
        .fillna("Unavailable")
        .value_counts(dropna=False)
    )
    print(counts.to_string())

    print("\nLatest layer indicators by site/depth:")
    latest = (
        layer.sort_values("date")
        .groupby(["site_id", "depth_in"], as_index=False)
        .tail(1)
        [["site_id", "date", "depth_in", VWC_COLUMN, "FC", "WP", "SAT", "FAW_layer", "layer_stress_category", "layer_alert_color"]]
        .sort_values(["site_id", "depth_in"])
    )
    print(latest.to_string(index=False))

    if root_depth_in is None:
        print(
            "\nROOT-ZONE ALERT NOT YET GENERATED: crop rooting depth has not been supplied. "
            "Layer-level indicators are complete where SSURGO hydraulic values exist."
        )
    else:
        print("\nLatest root-zone alerts:")
        latest_root = (
            root.sort_values("date")
            .groupby("site_id", as_index=False)
            .tail(1)
            .sort_values("site_id")
        )
        print(latest_root.to_string(index=False))


if __name__ == "__main__":
    main()
