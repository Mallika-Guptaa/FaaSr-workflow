import json
import os
import pandas as pd


def load_site_metadata(folder: str, input1: str, input2: str, output1: str, output2: str) -> None:
    local_csv = "sites_vwc_corrected.csv"
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_csv)
    faasr_log(f"Loaded {len(df)} rows from {input1}")

    if "Site" not in df.columns:
        msg = f"CSV missing required 'Site' column; found: {list(df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)

    # Load site_locations.json for lat/lon and optional start/end dates
    local_locs = "site_locations.json"
    faasr_get_file(local_file=local_locs, remote_folder=folder, remote_file=input2)
    with open(local_locs) as f:
        site_locations = json.load(f)
    faasr_log(f"Loaded {len(site_locations)} entries from {input2}")

    # Derive per-site date ranges: prefer site_locations.json, fall back to CSV Date min/max
    date_range_from_csv = {}
    if "Date" in df.columns:
        for sid, grp in df.groupby("Site"):
            dates = pd.to_datetime(grp["Date"], errors="coerce").dropna()
            if not dates.empty:
                date_range_from_csv[str(sid).strip()] = {
                    "start_date": dates.min().strftime("%Y-%m-%d"),
                    "end_date": dates.max().strftime("%Y-%m-%d"),
                }

    # Collect unique site IDs from CSV (in first-seen order)
    site_ids = list(dict.fromkeys(str(s).strip() for s in df["Site"]))

    if not site_ids:
        msg = "No site records found in CSV"
        faasr_log(msg)
        raise ValueError(msg)

    if len(site_ids) < 4:
        msg = f"Expected at least 4 sites for 4 ranked instances, found {len(site_ids)}: {site_ids}"
        faasr_log(msg)
        raise ValueError(msg)

    records = []
    for sid in site_ids:
        if sid not in site_locations:
            msg = f"Site '{sid}' not found in {input2}; available: {list(site_locations.keys())}"
            faasr_log(msg)
            raise ValueError(msg)

        loc = site_locations[sid]
        if "latitude" not in loc or "longitude" not in loc:
            msg = f"site_locations.json entry for '{sid}' missing latitude or longitude"
            faasr_log(msg)
            raise ValueError(msg)

        # Resolve start/end dates: JSON > CSV-derived > hardcoded fallback
        start_date = (
            loc.get("start_date")
            or date_range_from_csv.get(sid, {}).get("start_date")
            or "2025-06-23"
        )
        end_date = (
            loc.get("end_date")
            or date_range_from_csv.get(sid, {}).get("end_date")
            or "2025-10-08"
        )

        records.append({
            "site_id": sid,
            "latitude": float(loc["latitude"]),
            "longitude": float(loc["longitude"]),
            "start_date": str(start_date),
            "end_date": str(end_date),
        })

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
    os.remove(local_locs)
