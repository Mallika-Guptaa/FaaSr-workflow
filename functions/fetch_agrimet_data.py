import io
import json
import os
import re

import pandas as pd
import requests


def fetch_agrimet_data(folder: str, input1: str, output1: str) -> None:
    local_meta = "sites_metadata.json"
    faasr_get_file(local_file=local_meta, remote_folder=folder, remote_file=input1)
    with open(local_meta) as f:
        json.load(f)  # validate it parses; content not used (station/dates are fixed)
    os.remove(local_meta)

    station = "BEWO"
    start_date = "2025-06-23"
    end_date = "2025-10-08"
    pcodes = ["PP", "MN", "MX", "MM", "ETOS", "ETRS", "SR", "UA"]
    col_map = {
        "PP": "precip_mm",
        "MN": "tmin_c",
        "MX": "tmax_c",
        "MM": "tmean_c",
        "ETOS": "eto_grass_mm",
        "ETRS": "eto_alfalfa_mm",
        "SR": "solar_rad_mj_m2",
        "UA": "wind_speed_m_s",
    }

    sy, sm, sd = start_date.split("-")
    ey, em, ed = end_date.split("-")

    # Per the API: repeated year/month/day pairs for begin and end dates
    params = [
        ("station", station),
        ("year", sy), ("month", str(int(sm))), ("day", str(int(sd))),
        ("year", ey), ("month", str(int(em))), ("day", str(int(ed))),
    ]
    for pcode in pcodes:
        params.append(("pcode", pcode))

    url = "https://www.usbr.gov/pn-bin/daily.pl"
    faasr_log(f"Fetching AgriMet data from {url} for station {station} {start_date} to {end_date}")
    resp = requests.get(url, params=params, timeout=120)
    if not resp.ok:
        msg = f"AgriMet request failed: HTTP {resp.status_code}"
        faasr_log(msg)
        raise RuntimeError(msg)

    text = resp.text
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Extract between BEGIN DATA and END DATA
    m = re.search(r"BEGIN DATA\s*(.*?)\s*END DATA", text, re.DOTALL | re.IGNORECASE)
    if not m:
        msg = "AgriMet response missing BEGIN DATA / END DATA markers"
        faasr_log(msg)
        raise RuntimeError(msg)

    data_block = m.group(1).strip()
    if not data_block:
        msg = "AgriMet response has empty data block"
        faasr_log(msg)
        raise RuntimeError(msg)

    df = pd.read_csv(
        io.StringIO(data_block),
        sep=",",
        header=0,
        na_values=["--", "NO RECORD", ""],
        skipinitialspace=True,
    )
    df.columns = [c.strip() for c in df.columns]

    if df.empty:
        msg = "No usable dates parsed from AgriMet response"
        faasr_log(msg)
        raise RuntimeError(msg)

    # First column is DATE
    date_col = df.columns[0]
    df = df.rename(columns={date_col: "Date"})

    # Columns are like "BEWO PP", "BEWO MN" etc. — map to target names
    rename = {}
    for col in df.columns:
        upper = col.strip().upper()
        # strip station prefix if present (e.g. "BEWO PP" -> "PP")
        if upper.startswith(station + " "):
            pcode = upper[len(station) + 1:].strip()
        else:
            pcode = upper
        if pcode in col_map:
            rename[col] = col_map[pcode]
    df = df.rename(columns=rename)

    df.insert(1, "station", station)

    final_cols = [
        "Date", "station", "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    for c in final_cols:
        if c not in df.columns:
            df[c] = float("nan")
    df = df[final_cols]

    faasr_log(f"Parsed {len(df)} date rows for station {station}")

    local_csv = "agrimet.csv"
    df.to_csv(local_csv, index=False)
    faasr_put_file(local_file=local_csv, remote_folder=folder, remote_file=output1)
    faasr_log(f"Wrote {output1} ({len(df)} rows)")
    os.remove(local_csv)
