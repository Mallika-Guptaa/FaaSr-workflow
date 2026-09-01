import os

import pandas as pd


def merge_and_finalize(folder: str, input1: str, input2: str, input3: str, input4: str, input5: str, input6: str, output1: str) -> None:
    # Read AgriMet data
    local_agrimet = "agrimet.csv"
    faasr_get_file(local_file=local_agrimet, remote_folder=folder, remote_file=input1)
    agrimet_df = pd.read_csv(local_agrimet)
    os.remove(local_agrimet)
    faasr_log(f"Loaded AgriMet data: {len(agrimet_df)} rows")

    # Read VWC site metadata CSV
    local_sites = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_sites, remote_folder=folder, remote_file=input6)
    sites_df = pd.read_csv(local_sites)
    os.remove(local_sites)
    faasr_log(f"Loaded site metadata: {len(sites_df)} sites")

    # Discover all gridmet_site_*.csv files via faasr_get_folder_list
    all_keys = faasr_get_folder_list(prefix=folder)
    gridmet_keys = [k for k in all_keys if k.rsplit("/", 1)[-1].startswith("gridmet_site_") and k.rsplit("/", 1)[-1].endswith(".csv")]
    faasr_log(f"Discovered {len(gridmet_keys)} gridmet shard files: {gridmet_keys}")

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

    # Normalize date columns for joining
    # AgriMet dates may be MM/DD/YYYY; normalize to YYYY-MM-DD
    agrimet_df["Date"] = pd.to_datetime(agrimet_df["Date"]).dt.strftime("%Y-%m-%d")
    gridmet_df["Date"] = pd.to_datetime(gridmet_df["Date"]).dt.strftime("%Y-%m-%d")

    # AgriMet has a 'station' column; sites_vwc_corrected has 'site_id' and 'station' may differ.
    # Join sites metadata to agrimet on station == site_id to expand to per-site rows.
    # Then join with gridmet on (site_id, Date).
    agrimet_prefixed = agrimet_df.rename(columns={
        "precip_mm": "agrimet_precip_mm",
        "tmin_c": "agrimet_tmin_c",
        "tmax_c": "agrimet_tmax_c",
        "tmean_c": "agrimet_tmean_c",
        "eto_grass_mm": "agrimet_eto_grass_mm",
        "eto_alfalfa_mm": "agrimet_eto_alfalfa_mm",
        "solar_rad_mj_m2": "agrimet_solar_rad_mj_m2",
        "wind_speed_m_s": "agrimet_wind_speed_m_s",
    })

    # Cross-join sites metadata with agrimet dates, then merge gridmet per site-date
    # Each site shares the single BEWO agrimet record for the same date.
    agrimet_prefixed = agrimet_prefixed.rename(columns={"station": "agrimet_station"})

    # Expand: for each site, attach all agrimet rows
    site_agrimet = sites_df.merge(agrimet_prefixed, how="cross")

    # Merge with gridmet on (site_id, Date)
    merged = site_agrimet.merge(
        gridmet_df,
        on=["site_id", "Date"],
        how="left",
    )

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
