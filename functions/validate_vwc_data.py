import tempfile
import os
import pandas as pd


def validate_vwc_data(folder: str, input1: str, output1: str) -> None:
    faasr_log("Downloading input file: " + input1)

    with tempfile.TemporaryDirectory() as tmpdir:
        local_input = os.path.join(tmpdir, input1)
        faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)

        faasr_log("Reading CSV and validating columns")
        df = pd.read_csv(local_input)

        required_columns = {"Date", "Depth (in)", "Volumetric Water content (cm3/cm3)", "Site"}
        missing = required_columns - set(df.columns)
        if missing:
            msg = "Missing required columns: " + str(missing)
            faasr_log(msg)
            raise ValueError(msg)

        faasr_log("Parsing Date column as datetime")
        df["Date"] = pd.to_datetime(df["Date"])

        faasr_log("Sorting rows by Date ascending")
        df = df.sort_values("Date").reset_index(drop=True)

        local_output = os.path.join(tmpdir, output1)
        df.to_csv(local_output, index=False)

        faasr_log("Uploading validated file: " + output1)
        faasr_put_file(local_file=local_output, remote_folder=folder, remote_file=output1)

    faasr_log("validate_vwc_data complete")
