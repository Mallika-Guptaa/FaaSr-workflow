import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tempfile
import os

def create_plot(folder: str, input1: str, output1: str) -> None:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_in:
        tmp_in_path = tmp_in.name

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_out:
        tmp_out_path = tmp_out.name

    try:
        faasr_get_file(local_file=tmp_in_path, remote_folder=folder, remote_file=input1)
        df = pd.read_csv(tmp_in_path)
        faasr_log(f"Read {len(df)} points from {input1}")

        fig, ax = plt.subplots()
        ax.plot(df["x"], df["y"], marker="o")
        ax.set_title("y = 2x + 3")
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        fig.savefig(tmp_out_path)
        plt.close(fig)

        faasr_put_file(local_file=tmp_out_path, remote_folder=folder, remote_file=output1)
        faasr_log(f"Uploaded {output1} to {folder}")
    finally:
        os.unlink(tmp_in_path)
        os.unlink(tmp_out_path)
