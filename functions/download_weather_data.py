import os
import json
import tempfile
import datetime
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------
ACTION2_FOLDER = "data/action2"
DATE_START = "2025-06-23"
DATE_END = "2025-10-08"
REQUEST_TIMEOUT = 90
MAX_RETRIES = 3

AGRIMET_STATION = "BEWO"

# AgriMet pcodes → output column names
AGRIMET_PCODE_MAP = {
    "PP": "precip_mm",
    "MN": "tmin_c",
    "MX": "tmax_c",
    "MM": "tmean_c",
    "ET": "et_grass_mm",
    "ER": "et_alfalfa_mm",
    "SR": "solar_rad_mj_m2",
    "YM": "wind_speed_m_s",
}

# gridMET variables via Climate Engine
GRIDMET_VARS = "pr, tmmn, tmmx, srad, vs, eto, etr"

CLIMATE_ENGINE_BASE = "https://api.climateengine.org"


# -----------------------------------------------------------------------
# Retry decorator
# -----------------------------------------------------------------------
def _make_retry():
    return retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )


# -----------------------------------------------------------------------
# AgriMet fetch (USBR Pacific Northwest Hydromet)
# -----------------------------------------------------------------------
@_make_retry()
def _fetch_agrimet(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch daily AgriMet BEWO data via the USBR webdaycsv endpoint."""
    pcodes = list(AGRIMET_PCODE_MAP.keys())
    pcode_str = " ".join(pcodes)

    # USBR Pacific Northwest AgriMet daily CSV endpoint
    resp = requests.get(
        "https://www.usbr.gov/pn-bin/webdaycsv.pl",
        params={
            "station": AGRIMET_STATION,
            "pc": pcode_str,
            "start": start_date,
            "stop": end_date,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    text = resp.text
    # Detect and raise on HTML error pages
    if "<html" in text.lower() or "error" in text.lower()[:200]:
        raise requests.exceptions.RequestException(
            f"AgriMet returned an error page: {text[:300]}"
        )

    return _parse_agrimet_csv(text, start_date, end_date)


def _parse_agrimet_csv(text: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Parse the AgriMet CSV response (may contain comment/header lines)."""
    lines = text.splitlines()

    # Find header row (contains 'Date' or station/pcode column headers)
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if "date" in low or any(
            f"{AGRIMET_STATION}{p}" in stripped.upper() for p in AGRIMET_PCODE_MAP
        ):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            f"Cannot find header in AgriMet response. First 10 lines:\n"
            + "\n".join(lines[:10])
        )

    data_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(data_text))
    df.columns = [c.strip() for c in df.columns]

    # Find the date column
    date_col = next((c for c in df.columns if "date" in c.lower()), None)
    if date_col is None:
        raise ValueError(f"No date column found. Columns: {list(df.columns)}")
    df["date"] = pd.to_datetime(df[date_col], errors="raise").dt.strftime("%Y-%m-%d")

    # Map pcode-suffixed column names to standard names
    rename = {}
    for pcode, col_name in AGRIMET_PCODE_MAP.items():
        for raw_col in df.columns:
            up = raw_col.upper()
            if up == f"{AGRIMET_STATION}{pcode}" or up == pcode:
                rename[raw_col] = col_name
                break

    df = df.rename(columns=rename)

    keep_cols = ["date"] + [
        c
        for c in [
            "precip_mm", "tmin_c", "tmax_c", "tmean_c",
            "et_grass_mm", "et_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
        ]
        if c in df.columns
    ]
    df = df[keep_cols].copy()
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()
    df = df.sort_values("date").reset_index(drop=True)
    return df


# -----------------------------------------------------------------------
# gridMET via Climate Engine
# -----------------------------------------------------------------------
@_make_retry()
def _fetch_gridmet_site(site_id: int, lat: float, lon: float,
                        start_date: str, end_date: str,
                        api_key: str) -> pd.DataFrame:
    """Fetch daily gridMET for one site via Climate Engine POST endpoint."""
    # Climate Engine API: apiKey goes directly in Authorization header (no Bearer prefix)
    # Endpoint: POST /timeseries/native/coordinates
    # coordinates: JSON string of [[lon, lat]] (note: lon first)
    payload = {
        "coordinates": f"[[{lon}, {lat}]]",
        "dataset": "GRIDMET",
        "variable": GRIDMET_VARS,
        "start_date": start_date,
        "end_date": end_date,
    }
    resp = requests.post(
        f"{CLIMATE_ENGINE_BASE}/timeseries/native/coordinates",
        json=payload,
        headers={"Authorization": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return _parse_gridmet_response(data, site_id, start_date, end_date)


def _parse_gridmet_response(data: dict, site_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """Parse Climate Engine timeseries response into a DataFrame.

    The response may be shaped as:
      {"Data": {"pr": [{"date": "...", "value": ...}, ...]}}
    or as a list of date-keyed dicts, etc.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response type for site {site_id}: {type(data)}")

    # Handle top-level 'Data' wrapper
    payload = data.get("Data", data)

    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"No data in Climate Engine response for site {site_id}: {str(data)[:300]}")

    frames = []
    for var, records in payload.items():
        if not records:
            continue
        if isinstance(records, list):
            tmp = pd.DataFrame(records)
        elif isinstance(records, dict):
            # May be {"dates": [...], "values": [...]}
            tmp = pd.DataFrame({"date": records.get("dates", []), var: records.get("values", [])})
        else:
            continue

        # Normalize date column name
        date_col = next(
            (c for c in tmp.columns if c.lower() == "date"), None
        )
        if date_col is None:
            continue
        if date_col != "date":
            tmp = tmp.rename(columns={date_col: "date"})
        tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        tmp = tmp.dropna(subset=["date"])

        # Rename the value column to the variable name
        val_cols = [c for c in tmp.columns if c != "date"]
        if not val_cols:
            continue
        if val_cols[0] != var:
            tmp = tmp.rename(columns={val_cols[0]: var})

        frames.append(tmp[["date", var]].set_index("date"))

    if not frames:
        raise ValueError(f"No parseable gridMET variables for site {site_id}")

    df = frames[0]
    for f in frames[1:]:
        df = df.join(f, how="outer")
    df = df.reset_index()

    # Unit conversions
    # tmmn, tmmx: Kelvin → Celsius
    for col in ("tmmn", "tmmx"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") - 273.15
    # srad: W/m² → MJ/m²/day
    if "srad" in df.columns:
        df["srad"] = pd.to_numeric(df["srad"], errors="coerce") * 86400 / 1e6

    # Rename to standard names
    col_map = {
        "pr":   "precip_mm",
        "tmmn": "tmin_c",
        "tmmx": "tmax_c",
        "srad": "solar_rad_mj_m2",
        "vs":   "wind_speed_m_s",
        "eto":  "et_grass_mm",
        "etr":  "et_alfalfa_mm",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()
    df.insert(0, "site_id", site_id)
    df = df.sort_values("date").reset_index(drop=True)
    return df


# -----------------------------------------------------------------------
# S3 cache fallback helpers
# -----------------------------------------------------------------------
def _load_s3_agrimet(tmpdir: str, output1: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Load cached agrimet_daily.csv from S3; raise if absent or partial."""
    local = os.path.join(tmpdir, f"cached_{output1}")
    faasr_get_file(local_file=local, remote_folder=ACTION2_FOLDER, remote_file=output1)
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        raise RuntimeError("No S3 cache for agrimet_daily.csv")
    df = pd.read_csv(local)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    required = set(pd.date_range(start_date, end_date).strftime("%Y-%m-%d"))
    missing = required - set(df["date"].unique())
    if missing:
        raise RuntimeError(
            f"S3 agrimet cache covers only {len(df)} dates; "
            f"{len(missing)} required dates missing (e.g. {sorted(missing)[:5]})"
        )
    return df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()


def _load_s3_gridmet(tmpdir: str, output2: str, start_date: str,
                     end_date: str, site_ids: list) -> pd.DataFrame:
    """Load cached gridmet_daily_by_site.csv from S3; raise if absent or partial."""
    local = os.path.join(tmpdir, f"cached_{output2}")
    faasr_get_file(local_file=local, remote_folder=ACTION2_FOLDER, remote_file=output2)
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        raise RuntimeError("No S3 cache for gridmet_daily_by_site.csv")
    df = pd.read_csv(local)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    required = set(pd.date_range(start_date, end_date).strftime("%Y-%m-%d"))
    for sid in site_ids:
        missing = required - set(df[df["site_id"] == sid]["date"].unique())
        if missing:
            raise RuntimeError(
                f"S3 gridmet cache: site {sid} missing {len(missing)} dates "
                f"(e.g. {sorted(missing)[:5]})"
            )
    mask = (df["date"] >= start_date) & (df["date"] <= end_date)
    return df[mask].copy()


# -----------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------
def _validate_agrimet(df: pd.DataFrame) -> list:
    issues = []
    if df.duplicated(subset=["date"]).any():
        issues.append(f"agrimet: {df.duplicated(subset=['date']).sum()} duplicate dates")
    if "tmin_c" in df.columns and "tmax_c" in df.columns:
        bad = df.dropna(subset=["tmin_c", "tmax_c"])
        n_bad = (bad["tmin_c"] > bad["tmax_c"]).sum()
        if n_bad:
            issues.append(f"agrimet: {n_bad} rows where tmin > tmax")
    return issues


def _validate_gridmet(df: pd.DataFrame, site_ids: list, start_date: str, end_date: str) -> list:
    issues = []
    if df.duplicated(subset=["site_id", "date"]).any():
        issues.append(f"gridmet: {df.duplicated(subset=['site_id','date']).sum()} duplicate site/date rows")
    if "tmin_c" in df.columns and "tmax_c" in df.columns:
        bad = df.dropna(subset=["tmin_c", "tmax_c"])
        n_bad = (bad["tmin_c"] > bad["tmax_c"]).sum()
        if n_bad:
            issues.append(f"gridmet: {n_bad} rows where tmin > tmax")
    required = set(pd.date_range(start_date, end_date).strftime("%Y-%m-%d"))
    for sid in site_ids:
        gap = required - set(df[df["site_id"] == sid]["date"].unique())
        if gap:
            issues.append(f"gridmet: site {sid} missing {len(gap)} dates")
    return issues


# -----------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------
def download_weather_data(folder: str, input1: str, input2: str,
                          output1: str, output2: str, output3: str, output4: str) -> None:
    np.random.seed(42)
    tmpdir = tempfile.mkdtemp()

    # ------------------------------------------------------------------
    # 1. Load upstream inputs
    # ------------------------------------------------------------------
    local_sensor = os.path.join(tmpdir, "sensor_obs.csv")
    faasr_get_file(local_file=local_sensor, remote_folder=folder, remote_file=input1)
    if os.path.getsize(local_sensor) == 0:
        raise RuntimeError(f"Input file {input1} is empty or missing")
    sensor_df = pd.read_csv(local_sensor)
    sensor_df["date"] = pd.to_datetime(sensor_df["date"]).dt.strftime("%Y-%m-%d")
    faasr_log(f"Loaded {len(sensor_df)} sensor rows")

    local_sites = os.path.join(tmpdir, "site_locations.json")
    faasr_get_file(local_file=local_sites, remote_folder=folder, remote_file=input2)
    if os.path.getsize(local_sites) == 0:
        raise RuntimeError(f"Input file {input2} is empty or missing")
    with open(local_sites) as f:
        sites_json = json.load(f)
    sites = {int(k): v for k, v in sites_json.items()}
    site_ids = sorted(sites.keys())
    faasr_log(f"Loaded site_locations.json: {len(sites)} sites")

    start_date = DATE_START
    end_date = DATE_END
    faasr_log(f"Weather date range: {start_date} to {end_date}")

    # ------------------------------------------------------------------
    # 2. Fetch AgriMet (live → S3 cache on failure)
    # ------------------------------------------------------------------
    agrimet_source = "live"
    try:
        faasr_log("Fetching AgriMet BEWO daily data...")
        agrimet_df = _fetch_agrimet(start_date, end_date)
        faasr_log(f"AgriMet: {len(agrimet_df)} rows")
    except Exception as e:
        faasr_log(f"AgriMet live fetch failed ({type(e).__name__}: {e}). Trying S3 cache...")
        try:
            agrimet_df = _load_s3_agrimet(tmpdir, output1, start_date, end_date)
            agrimet_source = "s3_cache"
            faasr_log(f"AgriMet: {len(agrimet_df)} rows from S3 cache")
        except Exception as cache_err:
            raise RuntimeError(
                f"AgriMet fetch failed and S3 cache unavailable. "
                f"Live error: {e}. Cache error: {cache_err}"
            ) from e

    # ------------------------------------------------------------------
    # 3. Fetch gridMET via Climate Engine (4 parallel workers)
    # ------------------------------------------------------------------
    climate_api_key = faasr_secret("CLIMATE_API_KEY")

    gridmet_source = "live"
    gridmet_frames = []
    gridmet_errors = {}

    faasr_log("Fetching gridMET for all sites (4 workers)...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                _fetch_gridmet_site,
                sid,
                sites[sid]["latitude"],
                sites[sid]["longitude"],
                start_date,
                end_date,
                climate_api_key,
            ): sid
            for sid in site_ids
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                df = future.result()
                gridmet_frames.append(df)
                faasr_log(f"gridMET site {sid}: {len(df)} rows")
            except Exception as e:
                gridmet_errors[sid] = str(e)
                faasr_log(f"gridMET site {sid} failed: {e}")

    if gridmet_errors:
        faasr_log(
            f"gridMET live fetch failed for sites {list(gridmet_errors.keys())}. Trying S3 cache..."
        )
        try:
            gridmet_df_all = _load_s3_gridmet(tmpdir, output2, start_date, end_date, site_ids)
            gridmet_source = "s3_cache"
            faasr_log(f"gridMET: {len(gridmet_df_all)} rows from S3 cache")
        except Exception as cache_err:
            raise RuntimeError(
                f"gridMET live fetch failed for sites {list(gridmet_errors.keys())} "
                f"and S3 cache unavailable. Cache error: {cache_err}. "
                f"Live errors: {gridmet_errors}"
            ) from cache_err
    else:
        gridmet_df_all = pd.concat(gridmet_frames, ignore_index=True)
        gridmet_df_all = gridmet_df_all.sort_values(["site_id", "date"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # 4. Validate
    # ------------------------------------------------------------------
    agrimet_issues = _validate_agrimet(agrimet_df)
    gridmet_issues = _validate_gridmet(gridmet_df_all, site_ids, start_date, end_date)
    for issue in agrimet_issues + gridmet_issues:
        faasr_log(f"VALIDATION WARNING: {issue}")

    # ------------------------------------------------------------------
    # 5. Write CSVs
    # ------------------------------------------------------------------
    local_out1 = os.path.join(tmpdir, output1)
    agrimet_df.to_csv(local_out1, index=False)
    faasr_log(f"agrimet_daily.csv: {len(agrimet_df)} rows")

    local_out2 = os.path.join(tmpdir, output2)
    gridmet_df_all.to_csv(local_out2, index=False)
    faasr_log(f"gridmet_daily_by_site.csv: {len(gridmet_df_all)} rows")

    # ------------------------------------------------------------------
    # 6. Per-site plots (VWC + weather overlay, one panel per depth + weather vars)
    # ------------------------------------------------------------------
    weather_panels = [
        ("precip_mm",        "Precip (mm)"),
        ("tmin_c",           "Tmin (°C)"),
        ("tmax_c",           "Tmax (°C)"),
        ("et_grass_mm",      "ET Grass (mm)"),
        ("solar_rad_mj_m2",  "Solar Rad (MJ/m²)"),
    ]

    plot_remote_files = []
    for site_rank in range(1, 5):
        site_sensor = sensor_df[sensor_df["site_id"] == site_rank].copy()
        depths = sorted(site_sensor["depth_in"].unique())
        n_depths = len(depths)
        site_gm = gridmet_df_all[gridmet_df_all["site_id"] == site_rank].sort_values("date").copy()

        n_panels = n_depths + len(weather_panels)
        fig, axes = plt.subplots(n_panels, 1, figsize=(12, 3 * n_panels), squeeze=False)
        fig.suptitle(f"Site {site_rank} — Action 2: Weather Download", fontsize=12)

        # VWC panels
        for row_i, depth in enumerate(depths):
            ax = axes[row_i][0]
            depth_df = site_sensor[site_sensor["depth_in"] == depth].sort_values("date")
            ax.plot(range(len(depth_df)), depth_df["vwc"].values,
                    label=f"VWC depth={depth}in", color="steelblue", linewidth=1.5)
            ax.set_title(f"Soil VWC — depth {depth} in")
            ax.set_ylabel("VWC (m³/m³)")
            ax.set_ylim(bottom=0)
            ax.legend(fontsize=8)

        # Weather panels from gridMET
        for wi, (wcol, wlabel) in enumerate(weather_panels):
            ax = axes[n_depths + wi][0]
            if wcol in site_gm.columns and not site_gm[wcol].isna().all():
                ax.plot(range(len(site_gm)), pd.to_numeric(site_gm[wcol], errors="coerce").values,
                        label=f"gridMET {wlabel}", color="darkorange", linewidth=1.2)
            ax.set_title(f"gridMET — {wlabel}")
            ax.set_ylabel(wlabel)
            ax.legend(fontsize=8)

        plt.tight_layout()
        plot_filename = output4.replace("{rank}", str(site_rank))
        local_plot = os.path.join(tmpdir, plot_filename)
        fig.savefig(local_plot, dpi=100)
        plt.close(fig)
        faasr_log(f"Plot: {plot_filename}")
        faasr_put_file(local_file=local_plot, remote_folder=ACTION2_FOLDER, remote_file=plot_filename)
        plot_remote_files.append(f"{ACTION2_FOLDER}/{plot_filename}")

    # ------------------------------------------------------------------
    # 7. Manifest
    # ------------------------------------------------------------------
    required_dates = sorted(pd.date_range(start_date, end_date).strftime("%Y-%m-%d"))
    agrimet_gaps = sorted(set(required_dates) - set(agrimet_df["date"].unique()))

    gridmet_coverage = {}
    for sid in site_ids:
        covered = set(gridmet_df_all[gridmet_df_all["site_id"] == sid]["date"].unique())
        gaps = sorted(set(required_dates) - covered)
        gridmet_coverage[str(sid)] = {"covered": len(covered), "gaps": gaps[:10]}

    manifest = {
        "action": "action2_download_weather_data",
        "date_range": {"start": start_date, "end": end_date},
        "agrimet": {
            "source": agrimet_source,
            "station": AGRIMET_STATION,
            "row_count": int(len(agrimet_df)),
            "columns": list(agrimet_df.columns),
            "coverage_gaps": agrimet_gaps[:20],
            "validation_issues": agrimet_issues,
        },
        "gridmet": {
            "source": gridmet_source,
            "provider": "Climate Engine API (GRIDMET)",
            "row_count": int(len(gridmet_df_all)),
            "columns": list(gridmet_df_all.columns),
            "coverage_by_site": gridmet_coverage,
            "validation_issues": gridmet_issues,
        },
        "outputs": {
            "agrimet_daily": f"{ACTION2_FOLDER}/{output1}",
            "gridmet_daily_by_site": f"{ACTION2_FOLDER}/{output2}",
            "weather_merge_manifest": f"{ACTION2_FOLDER}/{output3}",
            "plots": plot_remote_files,
        },
    }

    local_out3 = os.path.join(tmpdir, output3)
    with open(local_out3, "w") as f:
        json.dump(manifest, f, indent=2, allow_nan=False)

    # ------------------------------------------------------------------
    # 8. Upload
    # ------------------------------------------------------------------
    faasr_put_file(local_file=local_out1, remote_folder=ACTION2_FOLDER, remote_file=output1)
    faasr_log(f"Uploaded {output1}")
    faasr_put_file(local_file=local_out2, remote_folder=ACTION2_FOLDER, remote_file=output2)
    faasr_log(f"Uploaded {output2}")
    faasr_put_file(local_file=local_out3, remote_folder=ACTION2_FOLDER, remote_file=output3)
    faasr_log(f"Uploaded {output3}")

    faasr_log("download_weather_data complete")
