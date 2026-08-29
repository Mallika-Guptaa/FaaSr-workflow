import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def generate_site_plots(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    local_csv = "gsp_input.csv"

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    if not os.path.exists(local_csv) or os.path.getsize(local_csv) == 0:
        msg = f"Input file {input1} is missing or empty"
        faasr_log(msg)
        raise FileNotFoundError(msg)

    try:
        df = pd.read_csv(local_csv)
    except Exception as e:
        msg = f"Failed to parse {input1} as CSV: {e}"
        faasr_log(msg)
        raise

    faasr_log(f"CSV loaded: {len(df)} rows, columns: {list(df.columns)}")

    # Detect time column (case-insensitive)
    time_col = None
    for col in df.columns:
        if col.lower() in ("time", "date", "timestamp"):
            time_col = col
            break
    if time_col is None:
        msg = f"No time/date/timestamp column found in {list(df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)

    # Detect VWC column (case-insensitive)
    vwc_col = None
    for col in df.columns:
        if "vwc" in col.lower() or "volumetric" in col.lower():
            vwc_col = col
            break
    if vwc_col is None:
        msg = f"No VWC/volumetric column found in {list(df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)

    # Detect site column — any column that is not the time or VWC column
    site_col = None
    for col in df.columns:
        if col == time_col or col == vwc_col:
            continue
        if "site" in col.lower() or df[col].dtype == object:
            site_col = col
            break
    if site_col is None:
        msg = f"No site identifier column found in {list(df.columns)}"
        faasr_log(msg)
        raise ValueError(msg)

    faasr_log(f"Using time='{time_col}', vwc='{vwc_col}', site='{site_col}'")

    df[time_col] = pd.to_datetime(df[time_col])

    sites = df[site_col].unique()
    faasr_log(f"Found {len(sites)} unique sites: {list(sites)}")

    if len(sites) < 4:
        msg = f"Expected at least 4 sites but found {len(sites)}: {list(sites)}"
        faasr_log(msg)
        raise ValueError(msg)

    # Use exactly the first 4 sites (by order of first appearance)
    site_order = df[site_col].unique()[:4]
    outputs = [output1, output2, output3, output4]

    for rank, (site, out_name) in enumerate(zip(site_order, outputs), start=1):
        site_df = df[df[site_col] == site].sort_values(time_col)
        local_png = f"gsp_site_{rank}.png"

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(site_df[time_col], site_df[vwc_col], marker="o", linewidth=1.5, markersize=4)
        ax.set_xlabel("Time")
        ax.set_ylabel("VWC (Volumetric Water Content)")
        ax.set_title(f"VWC over Time — {site}")
        fig.autofmt_xdate()
        plt.tight_layout()
        fig.savefig(local_png, dpi=150)
        plt.close(fig)

        faasr_log(f"Uploading {out_name} for site '{site}'")
        faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=out_name)
        os.remove(local_png)

    os.remove(local_csv)
    faasr_log("generate_site_plots complete")
