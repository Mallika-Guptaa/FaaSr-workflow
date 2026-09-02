import os
import re
import time
import numpy as np
import pandas as pd
import requests
import xarray as xr

DATE_START = "2025-06-23"
DATE_END = "2025-10-08"

AGRIMET_URL = "https://www.usbr.gov/pn-bin/daily.pl"
AGRIMET_STATION = "BEWO"
AGRIMET_PCODES = ["PP", "MN", "MX", "MM", "ETOS", "ETRS", "SR", "UA"]

AGRIMET_COL_MAP = {
    "PP": "precip_mm",
    "MN": "tmin_c",
    "MX": "tmax_c",
    "MM": "tmean_c",
    "ETOS": "eto_grass_mm",
    "ETRS": "eto_alfalfa_mm",
    "SR": "solar_rad_mj_m2",
    "UA": "wind_speed_m_s",
}

SITES = [
    {"Site": 1, "latitude": 44.158669, "longitude": -121.394525},
    {"Site": 2, "latitude": 44.157778, "longitude": -121.395125},
    {"Site": 3, "latitude": 44.158756, "longitude": -121.400275},
    {"Site": 4, "latitude": 44.159150, "longitude": -121.402769},
]

GRIDMET_VAR_MAP = {
    "pr": "precip_mm",
    "tmmn": "tmin_c",
    "tmmx": "tmax_c",
    "pet": "eto_grass_mm",
    "etr": "eto_alfalfa_mm",
    "srad": "solar_rad_mj_m2",
    "vs": "wind_speed_m_s",
}

GRIDMET_BASE = (
    "https://thredds.northwestknowledge.net/thredds/dodsC/MET/{var}/{var}_2025.nc#fillmismatch"
)

WEATHER_COLS = [
    "precip_mm", "tmin_c", "tmax_c", "tmean_c",
    "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
]


def _retry(fn, retries, label):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            faasr_log(f"WARNING: {label} attempt {attempt + 1}/{retries} failed: {e}")
            if attempt == retries - 1:
                raise RuntimeError(f"{label} failed after {retries} attempts: {e}") from e
            time.sleep(5 * (attempt + 1))


def _to_date_strings(times):
    try:
        return pd.to_datetime(times).strftime("%Y-%m-%d").tolist()
    except Exception:
        return [f"{t.year:04d}-{t.month:02d}-{t.day:02d}" for t in times]


def _fetch_agrimet():
    params = [
        ("station", AGRIMET_STATION),
        ("year", "2025"), ("month", "6"), ("day", "23"),
        ("year", "2025"), ("month", "10"), ("day", "8"),
    ]
    for p in AGRIMET_PCODES:
        params.append(("pcode", p))

    def _do_fetch():
        faasr_log(f"Fetching AgriMet data from {AGRIMET_URL} for station {AGRIMET_STATION}")
        resp = requests.get(AGRIMET_URL, params=params, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(
                f"AgriMet endpoint returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.text

    text = _retry(_do_fetch, 3, "AgriMet fetch")

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Extract BEGIN DATA ... END DATA block
    match = re.search(r"BEGIN\s+DATA(.*?)END\s+DATA", text, re.DOTALL | re.IGNORECASE)
    if not match:
        raise RuntimeError(
            f"AgriMet response missing BEGIN DATA/END DATA markers. "
            f"Response snippet: {text[:500]}"
        )

    block = match.group(1).strip()
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    if not lines:
        raise RuntimeError("AgriMet data block is empty between BEGIN/END DATA markers")

    # Parse header: comma-separated "DATE, BEWO PP, BEWO MN, ..."
    header_line = lines[0]
    col_pat = re.compile(r"\b" + re.escape(AGRIMET_STATION) + r"\s+(\w+)\b")
    header_pcodes = col_pat.findall(header_line)
    if not header_pcodes:
        raise RuntimeError(
            f"Could not find column names in AgriMet header: {header_line[:300]}"
        )
    faasr_log(f"AgriMet response pcodes: {header_pcodes}")

    records = []
    for line in lines[1:]:
        # Data rows are comma-separated: "MM/DD/YYYY, val0, val1, ..."
        tokens = [t.strip() for t in line.split(",")]
        if len(tokens) < 1 + len(header_pcodes):
            continue
        date_raw = tokens[0]
        data_vals = tokens[1:]

        try:
            date_str = pd.to_datetime(date_raw).strftime("%Y-%m-%d")
        except Exception:
            continue

        row = {"Date": date_str, "station": AGRIMET_STATION}
        for pcode, val_str in zip(header_pcodes, data_vals):
            col = AGRIMET_COL_MAP.get(pcode)
            if col is None:
                continue
            try:
                row[col] = float(val_str)
            except (ValueError, TypeError):
                row[col] = np.nan
        records.append(row)

    if not records:
        raise RuntimeError(
            "AgriMet response parsed to zero rows — no usable dates returned"
        )

    df = pd.DataFrame(records)

    # Unit conversions (apply only to columns present)
    if "precip_mm" in df.columns:
        df["precip_mm"] = df["precip_mm"] * 25.4  # inches → mm
    for col in ["tmin_c", "tmax_c", "tmean_c"]:
        if col in df.columns:
            df[col] = (df[col] - 32.0) * 5.0 / 9.0  # °F → °C
    if "eto_grass_mm" in df.columns:
        df["eto_grass_mm"] = df["eto_grass_mm"] * 25.4  # inches → mm
    if "eto_alfalfa_mm" in df.columns:
        df["eto_alfalfa_mm"] = df["eto_alfalfa_mm"] * 25.4  # inches → mm
    if "solar_rad_mj_m2" in df.columns:
        df["solar_rad_mj_m2"] = df["solar_rad_mj_m2"] * 0.041868  # Langleys → MJ/m²
    if "wind_speed_m_s" in df.columns:
        df["wind_speed_m_s"] = df["wind_speed_m_s"] * 0.44704  # mph → m/s

    df = df.sort_values("Date").reset_index(drop=True)

    required = [
        "Date", "station", "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    for c in required:
        if c not in df.columns:
            df[c] = np.nan

    faasr_log(f"AgriMet: {len(df)} rows fetched")
    return df[required]


def _fetch_gridmet():
    site_dfs = []

    for site in SITES:
        site_id = site["Site"]
        site_lat = site["latitude"]
        site_lon = site["longitude"]
        faasr_log(f"Fetching gridMET data for site {site_id} (lat={site_lat}, lon={site_lon})")

        var_data = {}
        date_index = None

        for var, col in GRIDMET_VAR_MAP.items():
            url = GRIDMET_BASE.format(var=var)

            def _do_fetch(_var=var, _url=url, _lat=site_lat, _lon=site_lon):
                faasr_log(f"  Opening gridMET variable {_var}")
                ds = xr.open_dataset(_url, engine="netcdf4")

                lon_name = "lon" if "lon" in ds.coords else "longitude"
                lat_name = "lat" if "lat" in ds.coords else "latitude"

                lon_min = float(ds.coords[lon_name].values.min())
                sel_lon = _lon % 360 if lon_min >= 0 else _lon

                ds_pt = ds.sel({lat_name: _lat, lon_name: sel_lon}, method="nearest")

                t_name = None
                for tc in ["day", "time", "Date"]:
                    if tc in ds_pt.coords:
                        t_name = tc
                        break
                if t_name is None:
                    ds.close()
                    raise RuntimeError(f"No time coordinate found in gridMET {_var}")

                try:
                    ds_pt = ds_pt.sel({t_name: slice(DATE_START, DATE_END)})
                except Exception:
                    all_dates = _to_date_strings(ds_pt[t_name].values)
                    idx = [
                        i for i, d in enumerate(all_dates)
                        if DATE_START <= d <= DATE_END
                    ]
                    ds_pt = ds_pt.isel({t_name: idx})

                times_raw = ds_pt[t_name].values
                dates = _to_date_strings(times_raw)
                vals = ds_pt[_var].values.astype(float)
                ds.close()
                return dates, vals

            dates, vals = _retry(_do_fetch, 3, f"gridMET {var} site {site_id}")

            if date_index is None:
                date_index = dates
            var_data[col] = vals

        if not date_index:
            raise RuntimeError(
                f"gridMET returned no dates for site {site_id} — no usable gridMET dates"
            )

        df_site = pd.DataFrame({"Date": date_index})
        df_site["Site"] = site_id
        df_site["latitude"] = site_lat
        df_site["longitude"] = site_lon
        for col, vals in var_data.items():
            df_site[col] = vals

        site_dfs.append(df_site)

    df = pd.concat(site_dfs, ignore_index=True)

    # Unit conversions
    df["tmin_c"] = df["tmin_c"] - 273.15       # K → °C
    df["tmax_c"] = df["tmax_c"] - 273.15       # K → °C
    df["tmean_c"] = (df["tmin_c"] + df["tmax_c"]) / 2.0
    df["solar_rad_mj_m2"] = df["solar_rad_mj_m2"] * 0.0864  # W/m² → MJ/m²/day
    # pr, pet, etr already in mm; vs already in m/s

    required = [
        "Date", "Site", "latitude", "longitude",
        "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    for c in required:
        if c not in df.columns:
            df[c] = np.nan

    faasr_log(f"gridMET: {len(df)} rows fetched across {df['Site'].nunique()} sites")
    return df[required]


def download_weather_data(folder: str, input1: str, output1: str, output2: str, output3: str) -> None:
    local_vwc = "vwc_validated_dwd.csv"
    local_agrimet = "agrimet_local.csv"
    local_gridmet = "gridmet_local.csv"
    local_merged = "weather_merged_local.csv"

    # Load validated VWC data
    faasr_log(f"Loading {input1} from folder {folder}")
    faasr_get_file(local_file=local_vwc, remote_folder=folder, remote_file=input1)
    df_vwc = pd.read_csv(local_vwc)
    faasr_log(f"VWC data: {len(df_vwc)} rows")
    n_vwc = len(df_vwc)

    # Fetch AgriMet
    faasr_log("Starting AgriMet fetch")
    df_agrimet = _fetch_agrimet()

    # Fetch gridMET
    faasr_log("Starting gridMET fetch")
    df_gridmet = _fetch_gridmet()

    # Validate agrimet
    agrimet_required = [
        "Date", "station", "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    missing_ag = [c for c in agrimet_required if c not in df_agrimet.columns]
    if missing_ag:
        raise ValueError(f"agrimet.csv missing required columns: {missing_ag}")
    if df_agrimet.empty:
        raise ValueError("agrimet.csv is empty — no data returned from AgriMet")
    ag_dates = df_agrimet["Date"].tolist()
    if not any(DATE_START <= d <= DATE_END for d in ag_dates):
        raise ValueError(
            f"agrimet.csv contains no dates in {DATE_START} – {DATE_END}"
        )

    # Validate gridmet
    gridmet_required = [
        "Date", "Site", "latitude", "longitude",
        "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    missing_gm = [c for c in gridmet_required if c not in df_gridmet.columns]
    if missing_gm:
        raise ValueError(f"gridmet.csv missing required columns: {missing_gm}")
    if df_gridmet.empty:
        raise ValueError("gridmet.csv is empty — no data returned from gridMET")
    gm_sites = set(df_gridmet["Site"].unique())
    missing_sites = {1, 2, 3, 4} - gm_sites
    if missing_sites:
        raise ValueError(f"gridmet.csv missing data for sites: {missing_sites}")

    # Save agrimet and gridmet to local files
    df_agrimet.to_csv(local_agrimet, index=False)
    df_gridmet.to_csv(local_gridmet, index=False)

    # Build merged output: left-join VWC with agrimet (on Date), then gridmet (on Date+Site)
    df_ag_merge = df_agrimet.rename(columns={c: f"{c}_agrimet" for c in WEATHER_COLS})
    df_gm_merge = df_gridmet.rename(columns={c: f"{c}_gridmet" for c in WEATHER_COLS})

    df_merged = df_vwc.merge(df_ag_merge, on="Date", how="left")
    df_merged = df_merged.merge(df_gm_merge, on=["Date", "Site"], how="left")

    df_merged = df_merged.sort_values(
        ["Site", "Depth (in)", "Date"]
    ).reset_index(drop=True)

    # Validate merged row count
    if len(df_merged) != n_vwc:
        raise ValueError(
            f"weather_merged.csv has {len(df_merged)} rows but VWC source had {n_vwc} rows"
        )

    df_merged.to_csv(local_merged, index=False)

    # Upload all three outputs
    faasr_log(f"Uploading {output1} ({len(df_agrimet)} rows)")
    faasr_put_file(local_file=local_agrimet, remote_folder=folder, remote_file=output1)

    faasr_log(f"Uploading {output2} ({len(df_gridmet)} rows)")
    faasr_put_file(local_file=local_gridmet, remote_folder=folder, remote_file=output2)

    faasr_log(f"Uploading {output3} ({len(df_merged)} rows)")
    faasr_put_file(local_file=local_merged, remote_folder=folder, remote_file=output3)

    faasr_log("download_weather_data complete")

    for f in [local_vwc, local_agrimet, local_gridmet, local_merged]:
        if os.path.exists(f):
            os.remove(f)
