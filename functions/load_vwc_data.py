import os
import pandas as pd


def load_vwc_data(folder: str, input1: str, output1: str) -> None:
    local_in = "vwc_raw.csv"
    local_out = "vwc_validated.csv"

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_in, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_in)
    faasr_log(f"Loaded {len(df)} rows from {input1}")

    required_cols = ["Date", "Site", "Depth (in)", "Volumetric Water content (cm3/cm3)"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    faasr_log("Date column parsed and normalized to YYYY-MM-DD")

    df.to_csv(local_out, index=False)
    faasr_log(f"Writing validated data ({len(df)} rows) to {output1}")

    faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=output1)
    faasr_log("load_vwc_data complete")

    os.remove(local_in)
    os.remove(local_out)
