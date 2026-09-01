import json
import os

import pandas as pd


def merge_and_finalize(folder: str, input1: str, input2: str, input3: str, input4: str, output1: str) -> None:
    # Read AgriMet data
    local_agrimet = "agrimet.csv"
    faasr_get_file(local_file=local_agrimet, remote_folder=folder, remote_file=input1)
    agrimet_df = pd.read_csv(local_agrimet)
    os.remove(local_agrimet)
    faasr_log(f"Loaded AgriMet data: {len(agrimet_df)} rows")

    # Discover all gridmet_site_*.csv shards via faasr_get_folder_list
    all_keys = faasr_get_folder_list(prefix=folder)
    shard_pattern = input2.split("{rank}")[0]  # prefix before placeholder e.g. "gridmet_site_"
    gridmet_keys = [
        k for k in all_keys
        if k.rsplit("/", 1)[-1].startswith(shard_pattern) and k.rsplit("/", 1)[-1].endswith(".csv")
    ]
    faasr_log(f"Discovered {len(gridmet_keys)} gridmet shard files")

    gridmet_frames = []
    for key in sorted(gridmet_keys):
        basename = key.rsplit("/", 1)[-1]
        local_gm = basename
        faasr_get_file(local_file=local_gm, remote_folder=folder, remote_file=basename)
        gm_df = pd.read_csv(local_gm)
        os.remove(local_gm)
        faasr_log(f"Loaded {basename}: {len(gm_df)} rows")
        gridmet_frames.append(gm_df)

    if not gridmet_frames:
        msg = "No gridmet shard files found; cannot produce merged output"
        faasr_log(msg)
        raise RuntimeError(msg)

    gridmet_df = pd.concat(gridmet_frames, ignore_index=True)
    faasr_log(f"Combined gridMET data: {len(gridmet_df)} rows across {len(gridmet_frames)} sites")

    # Read VWC site CSV — 'Site' column (capital S), no start_date/end_date columns
    local_sites = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_sites, remote_folder=folder, remote_file=input3)
    sites_df = pd.read_csv(local_sites)
    os.remove(local_sites)
    if "Site" not in sites_df.columns:
        msg = f"sites CSV missing 'Site' column; found: {list(sites_df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)
    faasr_log(f"Loaded VWC site data: {len(sites_df)} rows")

    # Read site_locations.json for lat/lon keyed by site ID
    local_locs = "site_locations.json"
    faasr_get_file(local_file=local_locs, remote_folder=folder, remote_file=input4)
    with open(local_locs) as f:
        site_locations = json.load(f)
    os.remove(local_locs)
    faasr_log(f"Loaded {len(site_locations)} site location entries")

    # Normalize date columns to YYYY-MM-DD
    agrimet_df["Date"] = pd.to_datetime(agrimet_df["Date"]).dt.strftime("%Y-%m-%d")
    gridmet_df["Date"] = pd.to_datetime(gridmet_df["Date"]).dt.strftime("%Y-%m-%d")
    if "Date" in sites_df.columns:
        sites_df["Date"] = pd.to_datetime(sites_df["Date"]).dt.strftime("%Y-%m-%d")

    # Build site-date base from VWC CSV: use 'Site' as site_id and 'Date'
    vwc_base = sites_df[["Site", "Date"]].rename(columns={"Site": "site_id"}).drop_duplicates()

    # Add lat/lon from site_locations.json
    def get_lat(sid):
        entry = site_locations.get(sid, {})
        return float(entry["latitude"]) if "latitude" in entry else None

    def get_lon(sid):
        entry = site_locations.get(sid, {})
        return float(entry["longitude"]) if "longitude" in entry else None

    vwc_base = vwc_base.copy()
    vwc_base["latitude"] = vwc_base["site_id"].map(get_lat)
    vwc_base["longitude"] = vwc_base["site_id"].map(get_lon)

    # Prefix AgriMet columns to distinguish from gridMET columns
    agrimet_prefixed = agrimet_df.rename(columns={
        "station": "agrimet_station",
        "precip_mm": "agrimet_precip_mm",
        "tmin_c": "agrimet_tmin_c",
        "tmax_c": "agrimet_tmax_c",
        "tmean_c": "agrimet_tmean_c",
        "eto_grass_mm": "agrimet_eto_grass_mm",
        "eto_alfalfa_mm": "agrimet_eto_alfalfa_mm",
        "solar_rad_mj_m2": "agrimet_solar_rad_mj_m2",
        "wind_speed_m_s": "agrimet_wind_speed_m_s",
    })

    # Join vwc_base with agrimet on Date (all sites share the BEWO AgriMet station record)
    site_agrimet = vwc_base.merge(agrimet_prefixed, on="Date", how="left")

    # Join with gridMET on (site_id, Date)
    merged = site_agrimet.merge(gridmet_df, on=["site_id", "Date"], how="left")

    if merged.empty:
        msg = "Merge produced no rows — check that site_id and Date columns align"
        faasr_log(msg)
        raise RuntimeError(msg)

    faasr_log(f"Merged dataset: {len(merged)} rows, {len(merged.columns)} columns")

    local_out = "agrimet_gridmet_merged.csv"
    merged.to_csv(local_out, index=False)
    faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=output1)
    faasr_log(f"Wrote {output1} ({len(merged)} rows)")
    os.remove(local_out)
