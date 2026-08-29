import os
import pandas as pd


def load_csv(folder: str, input1: str, output1: str) -> None:
    local_input = "load_csv_input.csv"
    local_output = "load_csv_output.csv"

    faasr_log(f"Downloading {input1} from folder {folder}")
    faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)

    if not os.path.exists(local_input) or os.path.getsize(local_input) == 0:
        msg = f"Input file {input1} is missing or empty in folder {folder}"
        faasr_log(msg)
        raise FileNotFoundError(msg)

    try:
        df = pd.read_csv(local_input)
    except Exception as e:
        msg = f"Failed to parse {input1} as CSV: {e}"
        faasr_log(msg)
        raise

    if df.empty:
        msg = f"CSV file {input1} contains no data rows"
        faasr_log(msg)
        raise ValueError(msg)

    faasr_log(f"Loaded CSV with {len(df)} rows and columns: {list(df.columns)}")

    df.to_csv(local_output, index=False)

    faasr_log(f"Uploading {output1} to folder {folder}")
    faasr_put_file(local_file=local_output, remote_folder=folder, remote_file=output1)

    os.remove(local_input)
    os.remove(local_output)

    faasr_log(f"load_csv complete: {input1} -> {output1}")
