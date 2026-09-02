import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def generate_site_plots(
    folder: str,
    input1: str,
    output1: str,
    output2: str,
    output3: str,
    output4: str,
) -> None:
    local_vwc = "vwc_validated_gsp.csv"

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_vwc, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_vwc)
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

    outputs = {1: output1, 2: output2, 3: output3, 4: output4}

    for site_id, out_file in outputs.items():
        local_png = f"site_{site_id}_vwc_plot_local.png"
        df_site = df[df["Site"] == site_id].copy()

        fig, ax = plt.subplots(figsize=(10, 5))

        depths = sorted(df_site["Depth (in)"].unique())
        for depth in depths:
            df_depth = df_site[df_site["Depth (in)"] == depth].sort_values("Date")
            ax.plot(
                df_depth["Date"],
                df_depth["Volumetric Water content (cm3/cm3)"],
                marker="o",
                label=f"{depth} in",
            )

        ax.set_xlabel("Date")
        ax.set_ylabel("Volumetric Water Content (cm³/cm³)")
        ax.set_title(f"Site {site_id} — Volumetric Water Content by Depth")
        ax.legend(title="Depth (in)")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

        fig.savefig(local_png, dpi=150)
        plt.close(fig)

        faasr_log(f"Uploading {out_file} for site {site_id}")
        faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=out_file)
        os.remove(local_png)

    os.remove(local_vwc)
    faasr_log("generate_site_plots complete")
