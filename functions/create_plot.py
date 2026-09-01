def create_plot(folder: str, input1: str, output1: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import tempfile, os

    local_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    local_csv.close()
    local_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    local_png.close()

    try:
        faasr_get_file(local_file=local_csv.name, remote_folder=folder, remote_file=input1)
        df = pd.read_csv(local_csv.name)

        fig, ax = plt.subplots()
        ax.plot(df["x"], df["y"], marker="o")
        ax.set_title("y = 2x + 3")
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        fig.savefig(local_png.name)
        plt.close(fig)

        faasr_log(f"Plot saved, uploading as {output1}")
        faasr_put_file(local_file=local_png.name, remote_folder=folder, remote_file=output1)
    finally:
        os.unlink(local_csv.name)
        os.unlink(local_png.name)
