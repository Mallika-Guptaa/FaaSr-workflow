import json
import os
import pandas as pd

SITE_LOCATIONS = {
    1: {"latitude": 44.158669, "longitude": -121.394525},
    2: {"latitude": 44.157778, "longitude": -121.395125},
    3: {"latitude": 44.158756, "longitude": -121.400275},
    4: {"latitude": 44.159150, "longitude": -121.402769},
}


def create_site_locations_json(folder: str, input1: str, output1: str) -> None:
    local_vwc = "vwc_validated_cslj.csv"
    local_out = "site_locations_local.json"

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_vwc, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_vwc)
    observed_sites = sorted(df["Site"].dropna().astype(int).unique().tolist())
    faasr_log(f"Sites found in VWC data: {observed_sites}")

    missing = [s for s in SITE_LOCATIONS if s not in observed_sites]
    if missing:
        faasr_log(f"WARNING: sites not present in VWC data: {missing}")

    locations = [
        {
            "site": site_id,
            "latitude": coords["latitude"],
            "longitude": coords["longitude"],
        }
        for site_id, coords in sorted(SITE_LOCATIONS.items())
    ]

    with open(local_out, "w") as f:
        json.dump(locations, f, indent=2)

    faasr_log(f"Uploading {output1} with {len(locations)} site entries")
    faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=output1)
    faasr_log("create_site_locations_json complete")

    os.remove(local_vwc)
    os.remove(local_out)
