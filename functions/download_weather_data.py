import os
import tempfile
import pandas as pd
import numpy as np
import requests

START_DATE = "2025-06-23"
END_DATE   = "2025-10-08"
AGRIMET_STATION = "BEWO"

SITE_COORDS = {
    1: (44.158669, -121.394525),
    2: (44.157778, -121.395125),
    3: (44.158756, -121.400275),
    4: (44.159150, -121.402769),
}


def _fetch_agrimet() -> pd.DataFrame:
    """Fetch daily AgriMet data for station BEWO."""
    url = "https://api.agrimet.usbr.gov/api/v1/daily"
    params = {
        "stations": AGRIMET_STATION,
        "startDate": START_DATE,
        "endDate": END_DATE,
        "interval": "daily",
    }
    try:
        token = faasr_secret("AGRIMET_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
    except (KeyError, Exception):
        # No token env var available; try unauthenticated (public endpoint)
        headers = {}

    faasr_log(f"Fetching AgriMet data for station {AGRIMET_STATION} ({START_DATE} to {END_DATE})")
    resp = requests.get(url, params=params, headers=headers, timeout=60)
    if not resp.ok:
        msg = f"AgriMet API error {resp.status_code}: {resp.text[:200]}"
        faasr_log(msg)
        raise RuntimeError(msg)

    data = resp.json()
    # The AgriMet API returns a list of observation dicts.
    # Normalise to a flat DataFrame regardless of exact nesting.
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and "data" in data:
        records = data["data"]
    elif isinstance(data, dict) and "observations" in data:
        records = data["observations"]
    else:
        msg = f"Unexpected AgriMet response structure: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        faasr_log(msg)
        raise RuntimeError(msg)

    if not records:
        msg = f"AgriMet returned no records for station {AGRIMET_STATION}"
        faasr_log(msg)
        raise RuntimeError(msg)

    df = pd.json_normalize(records)

    # --- Column mapping heuristics (field names vary by API version) ---
    def _col(candidates):
        for c in candidates:
            for col in df.columns:
                if col.lower() == c.lower() or col.lower().endswith("." + c.lower()):
                    return col
        return None

    date_col   = _col(["date", "Date", "datetime", "DateTime", "observationDate"])
    precip_col = _col(["precip", "precipitation", "PCP", "PRECIP", "PP"])
    tmin_col   = _col(["tmin", "Tmin", "minTemp", "min_temp", "TMIN", "air_temp_min"])
    tmax_col   = _col(["tmax", "Tmax", "maxTemp", "max_temp", "TMAX", "air_temp_max"])
    tmean_col  = _col(["tmean", "Tmean", "meanTemp", "mean_temp", "TMEAN", "air_temp_mean", "avgTemp"])
    eto_grass_col   = _col(["eto", "ETO", "et_grass", "eto_grass", "ETOG", "evapotranspiration_grass"])
    eto_alfalfa_col = _col(["etoa", "ETOA", "et_alfalfa", "eto_alfalfa", "evapotranspiration_alfalfa"])
    solar_col  = _col(["solar", "Solar", "solar_rad", "solarRad", "SRAD", "sr"])
    wind_col   = _col(["wind", "Wind", "windspeed", "wind_speed", "WS", "ws"])

    if date_col is None:
        msg = f"Cannot identify date column in AgriMet response. Columns: {list(df.columns)}"
        faasr_log(msg)
        raise RuntimeError(msg)

    out = pd.DataFrame()
    out["Date"]   = pd.to_datetime(df[date_col]).dt.normalize()
    out["station"] = AGRIMET_STATION

    def _safe(col, factor=1.0):
        if col is None:
            return np.nan
        return pd.to_numeric(df[col], errors="coerce") * factor

    # Precipitation: convert inches → mm if needed (AgriMet typically returns inches)
    precip_raw = _safe(precip_col)
    out["precip_mm"] = precip_raw * 25.4  # inches to mm

    # Temperatures: detect unit from magnitude (°F typical for AgriMet, but may be °C)
    tmin_raw  = _safe(tmin_col)
    tmax_raw  = _safe(tmax_col)
    tmean_raw = _safe(tmean_col)

    def _f_to_c(s):
        # Use mean to guess unit: if most values > 40 assume °F
        med = s.median()
        if pd.notna(med) and med > 40:
            return (s - 32) * 5.0 / 9.0
        return s

    out["tmin_c"]  = _f_to_c(tmin_raw)
    out["tmax_c"]  = _f_to_c(tmax_raw)
    out["tmean_c"] = _f_to_c(tmean_raw)

    # ET: inches → mm
    out["eto_grass_mm"]   = _safe(eto_grass_col) * 25.4
    out["eto_alfalfa_mm"] = _safe(eto_alfalfa_col) * 25.4

    # Solar radiation: detect units (Langleys → MJ/m2: * 0.04184; W/m2 daily avg needs * 0.0864)
    solar_raw = _safe(solar_col)
    if solar_raw is not None and not solar_raw.isna().all():
        med = solar_raw.median()
        if pd.notna(med):
            if med > 1000:
                # Probably W/m2 mean → MJ/m2/day
                out["solar_rad_mj_m2"] = solar_raw * 0.0864
            elif med > 100:
                # Probably Langleys
                out["solar_rad_mj_m2"] = solar_raw * 0.04184
            else:
                out["solar_rad_mj_m2"] = solar_raw
        else:
            out["solar_rad_mj_m2"] = np.nan
    else:
        out["solar_rad_mj_m2"] = np.nan

    # Wind: mph → m/s
    wind_raw = _safe(wind_col)
    out["wind_speed_m_s"] = wind_raw * 0.44704

    out = out.sort_values("Date").reset_index(drop=True)
    faasr_log(f"AgriMet: {len(out)} daily rows for station {AGRIMET_STATION}")
    return out


def _fetch_gridmet_site(site: int, lat: float, lon: float) -> pd.DataFrame:
    """Fetch gridMET data for one site via the REST point query."""
    # gridMET REST API endpoint (University of Idaho)
    base_url = "https://gridmet.climatologylab.org/api/v1/point"
    # Variables: precipitation_amount, air_temperature (min/max), wind_speed,
    #            surface_downwelling_shortwave_flux_in_air, potential_evapotranspiration
    variables = [
        "precipitation_amount",
        "daily_minimum_temperature",
        "daily_maximum_temperature",
        "wind_speed",
        "surface_downwelling_shortwave_flux_in_air",
        "potential_evapotranspiration",
    ]

    frames = []
    for var in variables:
        params = {
            "lat": lat,
            "lon": lon,
            "start": START_DATE,
            "end": END_DATE,
            "variable": var,
            "unitType": "si",
        }
        faasr_log(f"  gridMET Site {site} variable={var}")
        resp = requests.get(base_url, params=params, timeout=60)
        if not resp.ok:
            msg = f"gridMET API error for Site {site} var={var}: {resp.status_code} {resp.text[:200]}"
            faasr_log(msg)
            raise RuntimeError(msg)
        data = resp.json()
        # Expected structure: {"data": [[date, value], ...]} or {"dates": [...], "values": [...]}
        if "data" in data:
            rows = data["data"]
            tmp = pd.DataFrame(rows, columns=["Date", var])
        elif "dates" in data and "values" in data:
            tmp = pd.DataFrame({"Date": data["dates"], var: data["values"]})
        else:
            msg = f"Unexpected gridMET response structure for var={var}: {list(data.keys())}"
            faasr_log(msg)
            raise RuntimeError(msg)
        tmp["Date"] = pd.to_datetime(tmp["Date"]).dt.normalize()
        tmp[var] = pd.to_numeric(tmp[var], errors="coerce")
        frames.append(tmp.set_index("Date"))

    df = pd.concat(frames, axis=1).reset_index()

    out = pd.DataFrame()
    out["Date"] = df["Date"]
    out["Site"] = site
    out["latitude"] = lat
    out["longitude"] = lon

    # Precipitation: mm (already SI)
    out["precip_mm"] = df.get("precipitation_amount", np.nan)

    # Temperatures: K → C
    tmin = df.get("daily_minimum_temperature", pd.Series(np.nan, index=df.index))
    tmax = df.get("daily_maximum_temperature", pd.Series(np.nan, index=df.index))
    tmin_c = pd.to_numeric(tmin, errors="coerce")
    tmax_c = pd.to_numeric(tmax, errors="coerce")
    # If values look like Kelvin (> 200), subtract 273.15
    if tmin_c.median() > 200:
        tmin_c = tmin_c - 273.15
    if tmax_c.median() > 200:
        tmax_c = tmax_c - 273.15
    out["tmin_c"] = tmin_c
    out["tmax_c"] = tmax_c
    out["tmean_c"] = (tmin_c + tmax_c) / 2.0

    # ETo grass (mm)
    out["eto_grass_mm"] = df.get("potential_evapotranspiration", np.nan)

    # Solar: W/m2 → MJ/m2/day
    solar = df.get("surface_downwelling_shortwave_flux_in_air", pd.Series(np.nan, index=df.index))
    solar_num = pd.to_numeric(solar, errors="coerce")
    out["solar_rad_mj_m2"] = solar_num * 0.0864

    # Wind: m/s (already SI)
    out["wind_speed_m_s"] = df.get("wind_speed", np.nan)

    return out.sort_values("Date").reset_index(drop=True)


def download_weather_data(folder: str, input1: str, output1: str, output2: str, output3: str) -> None:
    # ── 1. Download the VWC CSV ──────────────────────────────────────────────
    local_vwc = os.path.join(tempfile.gettempdir(), input1)
    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_vwc, remote_folder=folder, remote_file=input1)
    vwc_df = pd.read_csv(local_vwc)
    vwc_df["Date"] = pd.to_datetime(vwc_df["Date"]).dt.normalize()
    faasr_log(f"VWC CSV loaded: {len(vwc_df)} rows")

    # ── 2. Fetch AgriMet ─────────────────────────────────────────────────────
    agrimet_df = _fetch_agrimet()
    local_agrimet = os.path.join(tempfile.gettempdir(), output1)
    agrimet_df.to_csv(local_agrimet, index=False)
    faasr_put_file(local_file=local_agrimet, remote_folder=folder, remote_file=output1)
    faasr_log(f"Uploaded {output1} ({len(agrimet_df)} rows) to folder {folder}")

    # ── 3. Fetch gridMET for all four sites ──────────────────────────────────
    faasr_log("Fetching gridMET data for all four sites")
    gridmet_parts = []
    for site_id, (lat, lon) in SITE_COORDS.items():
        faasr_log(f"Fetching gridMET for Site {site_id} (lat={lat}, lon={lon})")
        part = _fetch_gridmet_site(site_id, lat, lon)
        gridmet_parts.append(part)
    gridmet_df = pd.concat(gridmet_parts, ignore_index=True)
    local_gridmet = os.path.join(tempfile.gettempdir(), output2)
    gridmet_df.to_csv(local_gridmet, index=False)
    faasr_put_file(local_file=local_gridmet, remote_folder=folder, remote_file=output2)
    faasr_log(f"Uploaded {output2} ({len(gridmet_df)} rows) to folder {folder}")

    # ── 4. Merge VWC + AgriMet + gridMET ────────────────────────────────────
    merged = vwc_df.copy()
    agrimet_merge = agrimet_df.drop(columns=["station"], errors="ignore")
    merged = merged.merge(
        agrimet_merge.add_suffix("_agrimet").rename(columns={"Date_agrimet": "Date"}),
        on="Date",
        how="left",
    )

    gridmet_merge = gridmet_df.drop(columns=["latitude", "longitude"], errors="ignore")
    # gridMET has (Date, Site); VWC has Site as integer
    gridmet_merge["Site"] = gridmet_merge["Site"].astype(vwc_df["Site"].dtype)
    merged = merged.merge(
        gridmet_merge.add_suffix("_gridmet").rename(
            columns={"Date_gridmet": "Date", "Site_gridmet": "Site"}
        ),
        on=["Date", "Site"],
        how="left",
    )

    local_merged = os.path.join(tempfile.gettempdir(), output3)
    merged.to_csv(local_merged, index=False)
    faasr_put_file(local_file=local_merged, remote_folder=folder, remote_file=output3)
    faasr_log(f"Uploaded {output3} ({len(merged)} rows) to folder {folder}")
