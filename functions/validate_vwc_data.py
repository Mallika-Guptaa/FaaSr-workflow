import pandas as pd
import tempfile
import os


def validate_vwc_data(folder: str, input1: str, output1: str) -> None:
    faasr_log(f"Starting validate_vwc_data: downloading {input1} from {folder}")

    local_input = tempfile.mktemp(suffix=".csv")
    local_output = tempfile.mktemp(suffix=".csv")

    try:
        faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)

        df = pd.read_csv(local_input)

        required_columns = ["Date", "Depth (in)", "Volumetric Water content (cm3/cm3)", "Site"]
        missing = [col for col in required_columns if col not in df.columns]
        if missing:
            msg = f"Missing required columns: {missing}"
            faasr_log(msg)
            raise ValueError(msg)

        faasr_log(f"All required columns present. Parsing Date and sorting.")

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)

        df.to_csv(local_output, index=False)

        faasr_put_file(local_file=local_output, remote_folder=folder, remote_file=output1)
        faasr_log(f"Uploaded validated data as {output1} to {folder}")

    finally:
        for f in (local_input, local_output):
            if os.path.exists(f):
                os.remove(f)
