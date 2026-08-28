import os
import json
import tempfile

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# Prescribed site mappings (verbatim from user request)
SITE_LOCATIONS = {
    1: {
        "site_id": 1,
        "latitude": 44.158669,
        "longitude": -121.394525,
        "dms_lat": "44°9'31.21\"N",
        "dms_lon": "121°23'40.29\"W",
    },
    2: {
        "site_id": 2,
        "latitude": 44.157778,
        "longitude": -121.395125,
        "dms_lat": "44°9'28.00\"N",
        "dms_lon": "121°23'42.45\"W",
    },
    3: {
        "site_id": 3,
        "latitude": 44.158756,
        "longitude": -121.400275,
        "dms_lat": "44°9'31.52\"N",
        "dms_lon": "121°24'0.99\"W",
    },
    4: {
        "site_id": 4,
        "latitude": 44.159150,
        "longitude": -121.402769,
        "dms_lat": "44°9'32.94\"N",
        "dms_lon": "121°24'9.97\"W",
    },
}

# Remote subfolder for all action-1 outputs
ACTION1_FOLDER = "data/action1"


def load_soil_inputs(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    np.random.seed(42)

    tmpdir = tempfile.mkdtemp()

    # ------------------------------------------------------------------
    # 1. Load raw sensor CSV
    # ------------------------------------------------------------------
    local_csv = os.path.join(tmpdir, "raw_sensor.csv")
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)
    faasr_log(f"Loaded raw sensor file: {input1}")

    if os.path.getsize(local_csv) == 0:
        raise RuntimeError(f"Input file {input1} is empty or missing from S3")

    df = pd.read_csv(local_csv)
    faasr_log(f"Raw rows: {len(df)}")

    # ------------------------------------------------------------------
    # 2. Validate required columns
    # ------------------------------------------------------------------
    required_cols = {"site_id", "depth_in", "date", "VWC"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # ------------------------------------------------------------------
    # 3. Coerce types
    # ------------------------------------------------------------------
    df["site_id"] = pd.to_numeric(df["site_id"], errors="raise").astype(int)
    df["depth_in"] = pd.to_numeric(df["depth_in"], errors="raise").astype(float)
    df["VWC"] = pd.to_numeric(df["VWC"], errors="raise").astype(float)
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.strftime("%Y-%m-%d")

    # Preserve original VWC in raw_vwc; working column vwc is identical (Action 1 must not modify)
    df["raw_vwc"] = df["VWC"]
    df["vwc"] = df["VWC"]
    df = df.drop(columns=["VWC"])

    # ------------------------------------------------------------------
    # 4. Validate site mapping (each record maps to exactly one site)
    # ------------------------------------------------------------------
    known_sites = set(SITE_LOCATIONS.keys())
    unknown = set(df["site_id"].unique()) - known_sites
    if unknown:
        raise ValueError(f"Sensor records contain unknown site_ids: {unknown}")

    # Attach WGS84 lat/lon
    df["latitude"] = df["site_id"].map(lambda s: SITE_LOCATIONS[s]["latitude"])
    df["longitude"] = df["site_id"].map(lambda s: SITE_LOCATIONS[s]["longitude"])

    # ------------------------------------------------------------------
    # 5. Reject duplicate composite keys (site_id / date / depth_in)
    # ------------------------------------------------------------------
    dup_mask = df.duplicated(subset=["site_id", "date", "depth_in"], keep=False)
    n_dup = dup_mask.sum()
    if n_dup > 0:
        dup_rows = df[dup_mask][["site_id", "date", "depth_in"]].to_dict(orient="records")
        faasr_log(f"ERROR: {n_dup} duplicate composite keys found")
        raise ValueError(f"Duplicate site_id/date/depth_in keys detected: {dup_rows[:10]}")

    # ------------------------------------------------------------------
    # 6. QC flags (record, do NOT alter vwc)
    # ------------------------------------------------------------------
    df["qc_negative_vwc"] = df["vwc"] < 0
    df["qc_out_of_range_vwc"] = (df["vwc"] < 0) | (df["vwc"] > 1)

    n_negative = int(df["qc_negative_vwc"].sum())
    n_out_of_range = int(df["qc_out_of_range_vwc"].sum())
    faasr_log(f"QC: {n_negative} negative VWC, {n_out_of_range} out-of-range VWC (unflagged 0-1 bounds)")

    # ------------------------------------------------------------------
    # 7. Stable sort
    # ------------------------------------------------------------------
    df = df.sort_values(["site_id", "depth_in", "date"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # 8. Write standardized CSV
    # ------------------------------------------------------------------
    local_out1 = os.path.join(tmpdir, output1)
    df.to_csv(local_out1, index=False)
    faasr_log(f"Standardized CSV written: {len(df)} rows")

    # ------------------------------------------------------------------
    # 9. Build and write site_locations.json
    # ------------------------------------------------------------------
    site_json = {}
    for sid, info in SITE_LOCATIONS.items():
        site_json[str(sid)] = {
            "site_id": info["site_id"],
            "latitude": info["latitude"],
            "longitude": info["longitude"],
            "dms_lat": info["dms_lat"],
            "dms_lon": info["dms_lon"],
        }

    local_out2 = os.path.join(tmpdir, output2)
    with open(local_out2, "w") as f:
        json.dump(site_json, f, indent=2, allow_nan=False)
    faasr_log("site_locations.json written")

    # ------------------------------------------------------------------
    # 10. Generate four per-site comparison plots
    # ------------------------------------------------------------------
    plot_remote_files = []
    for site_rank in range(1, 5):
        site_df = df[df["site_id"] == site_rank].copy()
        depths = sorted(site_df["depth_in"].unique())
        n_depths = len(depths)

        if n_depths == 0:
            faasr_log(f"WARNING: no data for site {site_rank}, skipping plot")
            continue

        fig, axes = plt.subplots(n_depths, 1, figsize=(10, 3 * n_depths), squeeze=False)
        fig.suptitle(f"Site {site_rank} — Action 1: Load & Standardize (VWC unchanged)", fontsize=12)

        for row_i, depth in enumerate(depths):
            ax = axes[row_i][0]
            depth_df = site_df[site_df["depth_in"] == depth].sort_values("date")
            dates_x = range(len(depth_df))
            ax.plot(dates_x, depth_df["raw_vwc"].values, label="Original VWC", linewidth=2, color="steelblue")
            ax.plot(dates_x, depth_df["vwc"].values, label="Processed VWC", linewidth=1.2, color="orange", linestyle="--")
            ax.set_title(f"Depth {depth} in")
            ax.set_ylabel("VWC (m³/m³)")
            ax.set_xlabel("Observation index")
            ax.legend(fontsize=8)
            ax.set_ylim(bottom=0)

        plt.tight_layout()

        plot_filename = output4.replace("{rank}", str(site_rank))
        local_plot = os.path.join(tmpdir, plot_filename)
        fig.savefig(local_plot, dpi=100)
        plt.close(fig)
        faasr_log(f"Plot saved: {plot_filename}")

        faasr_put_file(local_file=local_plot, remote_folder=ACTION1_FOLDER, remote_file=plot_filename)
        plot_remote_files.append(f"{ACTION1_FOLDER}/{plot_filename}")

    # ------------------------------------------------------------------
    # 11. Build staging manifest
    # ------------------------------------------------------------------
    manifest = {
        "action": "action1_load_soil_inputs",
        "input_file": input1,
        "row_count_raw": len(df),
        "row_count_standardized": len(df),
        "date_range": {
            "min": df["date"].min(),
            "max": df["date"].max(),
        },
        "sites": [int(s) for s in sorted(df["site_id"].unique())],
        "depths": [float(d) for d in sorted(df["depth_in"].unique())],
        "duplicate_check": {
            "composite_key": ["site_id", "date", "depth_in"],
            "duplicates_found": 0,
        },
        "qc_flags": {
            "negative_vwc_count": n_negative,
            "out_of_range_vwc_count": n_out_of_range,
            "note": "VWC values NOT altered in Action 1; flags only recorded for QC review",
        },
        "outputs": {
            "sensor_observations_standardized": f"{ACTION1_FOLDER}/{output1}",
            "site_locations": f"{ACTION1_FOLDER}/{output2}",
            "staging_manifest": f"{ACTION1_FOLDER}/{output3}",
            "plots": plot_remote_files,
        },
    }

    local_out3 = os.path.join(tmpdir, output3)
    with open(local_out3, "w") as f:
        json.dump(manifest, f, indent=2, allow_nan=False)
    faasr_log("Staging manifest written")

    # ------------------------------------------------------------------
    # 12. Upload CSV, JSON, manifest to S3
    # ------------------------------------------------------------------
    faasr_put_file(local_file=local_out1, remote_folder=ACTION1_FOLDER, remote_file=output1)
    faasr_log(f"Uploaded {output1}")

    faasr_put_file(local_file=local_out2, remote_folder=ACTION1_FOLDER, remote_file=output2)
    faasr_log(f"Uploaded {output2}")

    faasr_put_file(local_file=local_out3, remote_folder=ACTION1_FOLDER, remote_file=output3)
    faasr_log(f"Uploaded {output3}")

    faasr_log("load_soil_inputs complete")
