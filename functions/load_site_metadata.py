import json
import os
import pandas as pd


def load_site_metadata(folder: str, input1: str, input2: str, output1: str, output2: str) -> None:
    local_csv = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_csv)
    faasr_log(f"Loaded {len(df)} rows from {input1}")

    if "site" not in df.columns:
        msg = f"CSV missing required 'site' column; found: {list(df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)

    for col in ("start_date", "end_date"):
        if col not in df.columns:
            msg = f"CSV missing required column: {col}"
            faasr_log(msg)
            raise ValueError(msg)

    # One record per unique site_id (first occurrence of start/end date)
    seen = {}
    for _, row in df.iterrows():
        sid = str(row["site"]).strip()
        if sid not in seen:
            seen[sid] = {"start_date": str(row["start_date"]), "end_date": str(row["end_date"])}

    if not seen:
        msg = "No site records found in CSV"
        faasr_log(msg)
        raise ValueError(msg)

    site_ids = list(seen.keys())
    if len(site_ids) < 4:
        msg = f"Expected at least 4 sites for 4 ranked instances, found {len(site_ids)}: {site_ids}"
        faasr_log(msg)
        raise ValueError(msg)

    # Load per-site JSON files for lat/lon
    records = []
    for sid in site_ids:
        json_filename = input2.replace("{site_id}", sid)
        local_json = f"{sid}.json"
        faasr_get_file(local_file=local_json, remote_folder=folder, remote_file=json_filename)
        with open(local_json) as f:
            site_info = json.load(f)
        if "latitude" not in site_info or "longitude" not in site_info:
            msg = f"Site JSON for {sid} missing latitude or longitude; keys: {list(site_info.keys())}"
            faasr_log(msg)
            raise ValueError(msg)
        records.append({
            "site_id": sid,
            "latitude": float(site_info["latitude"]),
            "longitude": float(site_info["longitude"]),
            "start_date": seen[sid]["start_date"],
            "end_date": seen[sid]["end_date"],
        })
        os.remove(local_json)

    # Write combined metadata
    local_meta = "sites_metadata.json"
    with open(local_meta, "w") as f:
        json.dump(records, f, indent=2)
    faasr_put_file(local_file=local_meta, remote_folder=folder, remote_file=output1)
    faasr_log(f"Wrote {output1} with {len(records)} site records")
    os.remove(local_meta)

    # Write 4 per-rank location shards (fan-out to fetch_gridmet_data ×4)
    for i in range(1, 5):
        site_record = records[i - 1]
        location = {
            "site_id": site_record["site_id"],
            "latitude": site_record["latitude"],
            "longitude": site_record["longitude"],
            "start_date": site_record["start_date"],
            "end_date": site_record["end_date"],
        }
        local_loc = f"site_location_{i}.json"
        with open(local_loc, "w") as f:
            json.dump(location, f, indent=2)
        shard_name = output2.replace("{rank}", str(i))
        faasr_put_file(local_file=local_loc, remote_folder=folder, remote_file=shard_name)
        faasr_log(f"Wrote {shard_name} for site {site_record['site_id']}")
        os.remove(local_loc)

    os.remove(local_csv)
