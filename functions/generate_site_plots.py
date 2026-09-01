import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import tempfile


def generate_site_plots(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    local_csv = os.path.join(tempfile.gettempdir(), input1)

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_csv)
    df["Date"] = pd.to_datetime(df["Date"])

    outputs = {1: output1, 2: output2, 3: output3, 4: output4}

    for site_id, out_name in outputs.items():
        site_df = df[df["Site"] == site_id]
        if site_df.empty:
            msg = f"No data found for Site {site_id} in {input1}"
            faasr_log(msg)
            raise ValueError(msg)

        fig, ax = plt.subplots(figsize=(10, 6))

        for depth in sorted(site_df["Depth (in)"].unique()):
            depth_df = site_df[site_df["Depth (in)"] == depth].sort_values("Date")
            ax.plot(depth_df["Date"], depth_df["Volumetric Water content (cm3/cm3)"],
                    label=f"{depth} in", marker="o", markersize=3)

        ax.set_xlabel("Date")
        ax.set_ylabel("Volumetric Water content (cm3/cm3)")
        ax.set_title(f"Site {site_id} — Volumetric Water Content Over Time")
        ax.legend(title="Depth (in)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.autofmt_xdate()

        local_png = os.path.join(tempfile.gettempdir(), out_name)
        fig.savefig(local_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

        faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=out_name)
        faasr_log(f"Uploaded {out_name} to folder {folder}")

    faasr_log("All four site plots generated and uploaded")
