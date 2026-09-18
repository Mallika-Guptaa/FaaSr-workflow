#!/usr/bin/env python3
"""Extract SSURGO hydraulic properties for the four soil-moisture monitoring sites.

This standalone GitHub Actions step intentionally starts from the Action 7
bias-corrected dataset in S3. It does not use Action 8 temporal smoothing.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any

import boto3
import numpy as np
import pandas as pd
import requests
from botocore.exceptions import ClientError

BUCKET = os.getenv("SOIL_BUCKET", "faasr-bucket-smarttap-private")
REGION = os.getenv("SOIL_BUCKET_REGION", "us-east-1")

ACTION7_KEY = os.getenv(
    "ACTION7_KEY",
    "soil_sensor_processing/bias_correction/soil_moisture_bias_corrected.csv",
)
LOCATIONS_KEY = os.getenv(
    "LOCATIONS_KEY",
    "soil_sensor_processing/staging/site_locations.json",
)
OUTPUT_PREFIX = os.getenv("SSURGO_OUTPUT_PREFIX", "soil_sensor_processing/ssurgo").rstrip("/")

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
LOCAL_ARTIFACT_DIR = os.getenv("LOCAL_ARTIFACT_DIR", "artifacts/ssurgo")


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


def run_sda_query(sql: str, timeout: int = 90) -> pd.DataFrame:
    response = requests.post(
        SDA_URL,
        data={"query": sql, "format": "JSON+COLUMNNAME"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    table = payload.get("Table", [])
    if not table:
        return pd.DataFrame()
    return pd.DataFrame(table[1:], columns=table[0])


def point_query(longitude: float, latitude: float) -> str:
    wkt = f"point ({longitude:.10f} {latitude:.10f})"
    return f"""
SELECT
    mu.mukey,
    mu.musym,
    mu.muname,
    c.cokey,
    c.compname,
    c.comppct_r,
    c.majcompflag,
    ch.chkey,
    ch.hzname,
    ch.hzdept_r,
    ch.hzdepb_r,
    ch.sandtotal_r,
    ch.silttotal_r,
    ch.claytotal_r,
    ch.om_r,
    ch.dbthirdbar_r,
    ch.wsatiated_r,
    ch.wtenthbar_r,
    ch.wthirdbar_r,
    ch.wfifteenbar_r,
    (
        SELECT TOP 1 ctg.texture
        FROM chtexturegrp AS ctg
        WHERE ctg.chkey = ch.chkey
          AND ctg.rvindicator = 'Yes'
    ) AS texture
FROM mapunit AS mu
INNER JOIN component AS c
    ON mu.mukey = c.mukey
INNER JOIN chorizon AS ch
    ON c.cokey = ch.cokey
WHERE mu.mukey IN (
    SELECT mukey
    FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')
)
ORDER BY mu.mukey, c.comppct_r DESC, c.cokey, ch.hzdept_r
"""


def numericize(frame: pd.DataFrame) -> pd.DataFrame:
    numeric = [
        "comppct_r",
        "hzdept_r",
        "hzdepb_r",
        "sandtotal_r",
        "silttotal_r",
        "claytotal_r",
        "om_r",
        "dbthirdbar_r",
        "wsatiated_r",
        "wtenthbar_r",
        "wthirdbar_r",
        "wfifteenbar_r",
    ]
    frame = frame.copy()
    for col in numeric:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def is_sandy(texture: Any, sand_pct: Any, clay_pct: Any) -> bool:
    text = str(texture or "").strip().lower()
    if text in {"sand", "loamy sand"}:
        return True
    sand = pd.to_numeric(pd.Series([sand_pct]), errors="coerce").iloc[0]
    clay = pd.to_numeric(pd.Series([clay_pct]), errors="coerce").iloc[0]
    return bool(pd.notna(sand) and sand >= 85 and (pd.isna(clay) or clay < 10))


def hydraulic_values(row: pd.Series) -> dict[str, Any]:
    sat = row.get("wsatiated_r")
    wp = row.get("wfifteenbar_r")
    sandy = is_sandy(row.get("texture"), row.get("sandtotal_r"), row.get("claytotal_r"))

    if sandy and pd.notna(row.get("wtenthbar_r")):
        fc_raw = row.get("wtenthbar_r")
        fc_field = "wtenthbar_r"
        fc_tension = "10 kPa (1/10 bar)"
    else:
        fc_raw = row.get("wthirdbar_r")
        fc_field = "wthirdbar_r"
        fc_tension = "33 kPa (1/3 bar)"

    values = {
        "SAT": np.nan if pd.isna(sat) else float(sat) / 100.0,
        "FC": np.nan if pd.isna(fc_raw) else float(fc_raw) / 100.0,
        "WP": np.nan if pd.isna(wp) else float(wp) / 100.0,
        "FC_source_field": fc_field,
        "FC_tension": fc_tension,
    }
    values["hydraulic_valid"] = bool(
        np.isfinite(values["SAT"])
        and np.isfinite(values["FC"])
        and np.isfinite(values["WP"])
        and 0 <= values["WP"] < values["FC"] < values["SAT"] <= 1
    )
    return values


def choose_mapunit_and_component(raw: pd.DataFrame, site_id: int) -> tuple[str, pd.DataFrame]:
    mukeys = raw["mukey"].dropna().astype(str).unique().tolist()
    if len(mukeys) != 1:
        raise RuntimeError(
            f"Site {site_id} matched {len(mukeys)} map units ({mukeys}). "
            "This point may lie on a boundary and requires manual review."
        )

    mukey = mukeys[0]
    in_mapunit = raw[raw["mukey"].astype(str) == mukey].copy()

    components = (
        in_mapunit[["cokey", "compname", "comppct_r", "majcompflag"]]
        .drop_duplicates()
        .sort_values(["comppct_r", "cokey"], ascending=[False, True], na_position="last")
    )
    if components.empty:
        raise RuntimeError(f"No components returned for Site {site_id}, mukey {mukey}.")

    # Prefer a component explicitly marked major; within that set use largest comppct_r.
    major = components[
        components["majcompflag"].astype(str).str.strip().str.lower().eq("yes")
    ]
    chosen = (major if not major.empty else components).iloc[0]
    horizons = (
        in_mapunit[in_mapunit["cokey"].astype(str) == str(chosen["cokey"])]
        .copy()
        .sort_values(["hzdept_r", "hzdepb_r"])
    )
    if horizons.empty:
        raise RuntimeError(f"No horizons returned for Site {site_id}.")
    return mukey, horizons


def match_depth(horizons: pd.DataFrame, depth_cm: float) -> tuple[pd.Series, str]:
    valid = horizons[horizons["hzdept_r"].notna() & horizons["hzdepb_r"].notna()].copy()
    inside = valid[(valid["hzdept_r"] <= depth_cm) & (depth_cm < valid["hzdepb_r"])]
    if not inside.empty:
        return inside.iloc[0], "containing_horizon"

    if valid.empty:
        raise RuntimeError("No horizons with usable top/bottom depth values.")
    midpoint = (valid["hzdept_r"] + valid["hzdepb_r"]) / 2.0
    idx = (midpoint - depth_cm).abs().idxmin()
    return valid.loc[idx], "nearest_horizon_midpoint"


def main() -> None:
    action7 = pd.read_csv(io.BytesIO(read_s3_bytes(ACTION7_KEY)))
    required = {"site_id", "depth_in", "vwc_bias_corrected_cm3_cm3"}
    missing = sorted(required - set(action7.columns))
    if missing:
        raise RuntimeError(
            f"Action 7 dataset is missing required columns: {missing}. "
            "The workflow will not fall back to a smoothed dataset."
        )

    locations = json.loads(read_s3_bytes(LOCATIONS_KEY).decode("utf-8"))
    sites = locations.get("sites", [])
    if not sites:
        raise RuntimeError("No sites found in staged site_locations.json.")

    sensor_depths = {
        int(site): sorted(
            pd.to_numeric(group["depth_in"], errors="coerce")
            .dropna()
            .astype(float)
            .unique()
            .tolist()
        )
        for site, group in action7.groupby("site_id")
    }

    rows: list[dict[str, Any]] = []
    horizon_exports: list[pd.DataFrame] = []
    site_summary: list[dict[str, Any]] = []

    for site in sites:
        site_id = int(site["site_id"])
        lat = float(site["latitude"])
        lon = float(site["longitude"])
        depths = sensor_depths.get(site_id, [])
        if not depths:
            continue

        raw = numericize(run_sda_query(point_query(lon, lat)))
        if raw.empty:
            raise RuntimeError(f"SSURGO returned no records for Site {site_id}.")

        mukey, horizons = choose_mapunit_and_component(raw, site_id)
        horizon_export = horizons.copy()
        horizon_export.insert(0, "site_id", site_id)
        horizon_export.insert(1, "latitude", lat)
        horizon_export.insert(2, "longitude", lon)
        horizon_exports.append(horizon_export)

        first = horizons.iloc[0]
        for depth_in in depths:
            depth_cm = depth_in * 2.54
            horizon, match_method = match_depth(horizons, depth_cm)
            hydraulic = hydraulic_values(horizon)

            rows.append({
                "site_id": site_id,
                "site_name": site.get("site_name", f"Site {site_id}"),
                "latitude": lat,
                "longitude": lon,
                "sensor_depth_in": depth_in,
                "sensor_depth_cm": depth_cm,
                "mukey": str(horizon["mukey"]),
                "musym": horizon.get("musym"),
                "muname": horizon.get("muname"),
                "cokey": str(horizon["cokey"]),
                "component_name": horizon.get("compname"),
                "component_percent": horizon.get("comppct_r"),
                "major_component_flag": horizon.get("majcompflag"),
                "chkey": str(horizon["chkey"]),
                "horizon_name": horizon.get("hzname"),
                "horizon_top_cm": horizon.get("hzdept_r"),
                "horizon_bottom_cm": horizon.get("hzdepb_r"),
                "texture": horizon.get("texture"),
                "sand_percent": horizon.get("sandtotal_r"),
                "silt_percent": horizon.get("silttotal_r"),
                "clay_percent": horizon.get("claytotal_r"),
                "organic_matter_percent": horizon.get("om_r"),
                "bulk_density_g_cm3": horizon.get("dbthirdbar_r"),
                **hydraulic,
                "depth_match_method": match_method,
            })

        site_summary.append({
            "site_id": site_id,
            "latitude": lat,
            "longitude": lon,
            "mukey": mukey,
            "map_unit_name": str(first.get("muname")),
            "component_name": str(first.get("compname")),
            "component_percent": (
                None if pd.isna(first.get("comppct_r")) else float(first.get("comppct_r"))
            ),
            "sensor_depths_in": depths,
            "horizon_count": int(len(horizons)),
        })

    props = pd.DataFrame(rows)
    if props.empty:
        raise RuntimeError("No site-depth SSURGO property rows were produced.")

    horizons_all = pd.concat(horizon_exports, ignore_index=True)
    invalid = props[~props["hydraulic_valid"]].copy()

    manifest = {
        "status": "complete" if invalid.empty else "review_required",
        "input_dataset": f"s3://{BUCKET}/{ACTION7_KEY}",
        "smoothing_used": False,
        "source": "USDA-NRCS SSURGO / Soil Data Access",
        "sda_url": SDA_URL,
        "site_count": int(props["site_id"].nunique()),
        "site_depth_rows": int(len(props)),
        "invalid_hydraulic_rows": int(len(invalid)),
        "hydraulic_definition": {
            "SAT": "wsatiated_r / 100",
            "FC": "wtenthbar_r / 100 for sandy horizons when available; otherwise wthirdbar_r / 100",
            "WP": "wfifteenbar_r / 100",
        },
        "sites": site_summary,
    }

    props_csv = props.to_csv(index=False).encode("utf-8")
    horizons_csv = horizons_all.to_csv(index=False).encode("utf-8")
    manifest_json = json.dumps(manifest, indent=2).encode("utf-8")

    # Keep local copies so the GitHub workflow can upload a reviewable artifact.
    os.makedirs(LOCAL_ARTIFACT_DIR, exist_ok=True)
    with open(os.path.join(LOCAL_ARTIFACT_DIR, "ssurgo_site_depth_hydraulic_properties.csv"), "wb") as handle:
        handle.write(props_csv)
    with open(os.path.join(LOCAL_ARTIFACT_DIR, "ssurgo_dominant_component_horizons.csv"), "wb") as handle:
        handle.write(horizons_csv)
    with open(os.path.join(LOCAL_ARTIFACT_DIR, "ssurgo_extraction_manifest.json"), "wb") as handle:
        handle.write(manifest_json)

    write_s3_bytes(
        f"{OUTPUT_PREFIX}/ssurgo_site_depth_hydraulic_properties.csv",
        props_csv,
        "text/csv",
    )
    write_s3_bytes(
        f"{OUTPUT_PREFIX}/ssurgo_dominant_component_horizons.csv",
        horizons_csv,
        "text/csv",
    )
    write_s3_bytes(
        f"{OUTPUT_PREFIX}/ssurgo_extraction_manifest.json",
        manifest_json,
        "application/json",
    )

    print(json.dumps(manifest, indent=2))
    print("\nHydraulic property table:")
    print(props.to_string(index=False))

    if not invalid.empty:
        print(
            "\nWARNING: Some rows have missing or non-physical WP < FC < SAT ordering. "
            "These rows are retained and explicitly flagged for review."
        )


if __name__ == "__main__":
    main()
