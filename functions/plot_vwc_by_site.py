import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tempfile
import os


def plot_vwc_by_site(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    faasr_log(f"Starting plot_vwc_by_site: downloading {input1} from {folder}")

    local_input = tempfile.mktemp(suffix=".csv")
    output_files = [output1, output2, output3, output4]
    local_outputs = [tempfile.mktemp(suffix=".png") for _ in output_files]

    try:
        faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)

        df = pd.read_csv(local_input)
        df["Date"] = pd.to_datetime(df["Date"])

        for site_num, local_out, remote_out in zip([1, 2, 3, 4], local_outputs, output_files):
            site_df = df[df["Site"] == site_num]

            fig, ax = plt.subplots()
            for depth, depth_df in site_df.groupby("Depth (in)"):
                depth_df = depth_df.sort_values("Date")
                ax.plot(depth_df["Date"], depth_df["Volumetric Water content (cm3/cm3)"], label=f"{depth} in")

            ax.set_xlabel("Date")
            ax.set_ylabel("Volumetric Water content (cm3/cm3)")
            ax.set_title(f"Site {site_num} VWC Over Time")
            ax.legend(title="Depth (in)")
            fig.autofmt_xdate()
            fig.savefig(local_out)
            plt.close(fig)

            faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=remote_out)
            faasr_log(f"Uploaded {remote_out} to {folder}")

    finally:
        for f in [local_input] + local_outputs:
            if os.path.exists(f):
                os.remove(f)
