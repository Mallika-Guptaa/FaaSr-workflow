import json
import os
import pandas as pd


def load_site_metadata(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str, output5: str) -> None:
    local_csv = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_csv)
    faasr_log(f"Loaded {len(df)} sites from {input1}")

    required_cols = {"site_id", "start_date", "end_date", "latitude", "longitude"}
    missing = required_cols - set(df.columns)
    if missing:
        msg = f"CSV missing required columns: {missing}"
        faasr_log(msg)
        raise ValueError(msg)

    records = []
    for _, row in df.iterrows():
        records.append({
            "site_id": str(row["site_id"]),
            "start_date": str(row["start_date"]),
            "end_date": str(row["end_date"]),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
        })

    if not records:
        msg = "No site records found in CSV"
        faasr_log(msg)
        raise ValueError(msg)

    # Write full metadata JSON
    local_meta = "sites_metadata.json"
    with open(local_meta, "w") as f:
        json.dump(records, f, indent=2)
    faasr_put_file(local_file=local_meta, remote_folder=folder, remote_file=output1)
    faasr_log(f"Wrote {output1} with {len(records)} site records")

    # Write one per-site location JSON for each of the 4 ranked fetch_gridmet_data instances
    shard_outputs = [output2, output3, output4, output5]
    for i in range(1, 5):
        site_record = records[i - 1] if (i - 1) < len(records) else None
        if site_record is None:
            msg = f"No site record for rank {i} (only {len(records)} sites available)"
            faasr_log(msg)
            raise ValueError(msg)
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
        faasr_put_file(local_file=local_loc, remote_folder=folder, remote_file=shard_outputs[i - 1])
        faasr_log(f"Wrote {shard_outputs[i - 1]} for site {site_record['site_id']}")

    os.remove(local_csv)
    for i in range(1, 5):
        p = f"site_location_{i}.json"
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(local_meta):
        os.remove(local_meta)
