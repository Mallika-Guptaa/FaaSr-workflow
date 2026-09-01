import json
import os

import numpy as np
import pandas as pd
import xarray as xr


def fetch_gridmet_data(folder: str, input1: str, input2: str, output1: str) -> None:
    r = faasr_rank()
    rank = r["rank"]

    loc_file = input1.format(rank=rank)
    out_file = output1.format(rank=rank)

    # Read the per-site location JSON
    local_loc = f"site_location_{rank}.json"
    faasr_get_file(local_file=local_loc, remote_folder=folder, remote_file=loc_file)
    with open(local_loc) as f:
        loc = json.load(f)
    os.remove(local_loc)

    site_id = loc["site_id"]
    lat = float(loc["latitude"])
    lon = float(loc["longitude"])
    start_date = str(loc["start_date"])
    end_date = str(loc["end_date"])

    faasr_log(f"Rank {rank}: fetching gridMET for site {site_id} lat={lat} lon={lon} {start_date} to {end_date}")

    # Read the API key (gridMET THREDDS requires it per spec)
    api_key = faasr_secret("GRIDMET_API_KEY")

    # Also read the sites CSV (to confirm the site exists)
    local_csv = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input2)
    sites_df = pd.read_csv(local_csv)
    os.remove(local_csv)
    if site_id not in sites_df["site_id"].astype(str).values:
        msg = f"site_id {site_id} not found in {input2}"
        faasr_log(msg)
        raise ValueError(msg)

    # gridMET variable names -> output column names
    var_map = {
        "precipitation_amount": "precip_mm",
        "tmmn": "tmin_c",
        "tmmx": "tmax_c",
        "tmmean": "tmean_c",
        "etr": "eto_alfalfa_mm",
        "eto": "eto_grass_mm",
        "srad": "solar_rad_mj_m2",
        "vs": "wind_speed_m_s",
    }

    base_url = "https://thredds.northwestknowledge.net/thredds/dodsC/MET/{var}/{var}_2025.nc#fillmismatch"

    series = {}
    time_index = None

    for var, col in var_map.items():
        url = base_url.format(var=var)
        faasr_log(f"Rank {rank}: opening {url}")
        ds = xr.open_dataset(url, engine="netcdf4")
        # Select the time range
        ds_sel = ds.sel(time=slice(start_date, end_date))
        # Select nearest grid cell; gridMET lon is typically -180..180
        ds_near = ds_sel.sel(lat=lat, lon=lon, method="nearest")
        # Load into memory (single cell only)
        data_var = list(ds_near.data_vars)[0]
        arr = ds_near[data_var].load().values
        times = ds_near.time.values
        ds.close()

        if len(arr) == 0:
            msg = f"No data returned for variable {var} in range {start_date} to {end_date}"
            faasr_log(msg)
            raise RuntimeError(msg)

        if time_index is None:
            time_index = pd.to_datetime(times)

        series[col] = arr.astype(float)
        faasr_log(f"Rank {rank}: {var} -> {col}, {len(arr)} values")

    if time_index is None or len(time_index) == 0:
        msg = f"No usable dates returned for site {site_id}"
        faasr_log(msg)
        raise RuntimeError(msg)

    df = pd.DataFrame({"Date": time_index.strftime("%Y-%m-%d")})
    df.insert(1, "site_id", site_id)
    for col in var_map.values():
        df[col] = series.get(col, np.nan)

    final_cols = [
        "Date", "site_id", "precip_mm", "tmin_c", "tmax_c", "tmean_c",
        "eto_grass_mm", "eto_alfalfa_mm", "solar_rad_mj_m2", "wind_speed_m_s",
    ]
    df = df[final_cols]

    faasr_log(f"Rank {rank}: writing {out_file} with {len(df)} rows for site {site_id}")
    local_out = f"gridmet_site_{rank}.csv"
    df.to_csv(local_out, index=False)
    faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=out_file)
    faasr_log(f"Rank {rank}: wrote {out_file}")
    os.remove(local_out)
