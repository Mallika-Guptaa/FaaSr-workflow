import pandas as pd
import os
import tempfile


def load_vwc_data(folder: str, input1: str, output1: str) -> None:
    local_csv = os.path.join(tempfile.gettempdir(), input1)

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_csv)
    required_columns = {"Site", "Date", "Volumetric Water content (cm3/cm3)", "Depth (in)"}
    missing = required_columns - set(df.columns)
    if missing:
        msg = f"Missing required columns in {input1}: {missing}"
        faasr_log(msg)
        raise ValueError(msg)

    faasr_log(f"Loaded {len(df)} rows from {input1}; sites: {sorted(df['Site'].unique())}")

    faasr_put_file(local_file=local_csv, remote_folder=folder, remote_file=output1)
    faasr_log(f"Uploaded {output1} to folder {folder}")
