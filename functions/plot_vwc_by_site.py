import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_vwc_by_site(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    faasr_log("Downloading validated VWC file: " + input1)

    os.makedirs("data", exist_ok=True)

    local_input = os.path.join("data", input1)
    faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)

    faasr_log("Reading CSV")
    df = pd.read_csv(local_input)
    df["Date"] = pd.to_datetime(df["Date"])

    outputs = {1: output1, 2: output2, 3: output3, 4: output4}

    for site_num, out_filename in outputs.items():
        faasr_log("Generating plot for Site " + str(site_num))
        site_df = df[df["Site"] == site_num]

        fig, ax = plt.subplots()
        for depth, depth_df in site_df.groupby("Depth (in)"):
            depth_df = depth_df.sort_values("Date")
            ax.plot(depth_df["Date"], depth_df["Volumetric Water content (cm3/cm3)"],
                    label=str(depth) + " in")

        ax.set_xlabel("Date")
        ax.set_ylabel("Volumetric Water content (cm3/cm3)")
        ax.set_title("Site " + str(site_num) + " — VWC by Depth")
        ax.legend(title="Depth (in)")
        fig.autofmt_xdate()

        local_plot = os.path.join("data", out_filename)
        fig.savefig(local_plot, bbox_inches="tight")
        plt.close(fig)

        faasr_log("Uploading plot: " + out_filename)
        faasr_put_file(local_file=local_plot, remote_folder=folder, remote_file=out_filename)

    faasr_log("plot_vwc_by_site complete")
