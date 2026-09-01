def validate_vwc_data(folder: str, input1: str, output1: str) -> None:
    import pandas as pd
    import tempfile
    import os

    local_in = os.path.join(tempfile.gettempdir(), input1)
    faasr_get_file(local_file=local_in, remote_folder='Data', remote_file=input1)

    df = pd.read_csv(local_in)

    required_columns = ['Date', 'Depth (in)', 'Volumetric Water content (cm3/cm3)', 'Site']
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        msg = f"validate_vwc_data: missing required columns: {missing}"
        faasr_log(msg)
        raise ValueError(msg)

    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date').reset_index(drop=True)

    local_out = os.path.join(tempfile.gettempdir(), output1)
    df.to_csv(local_out, index=False)

    faasr_put_file(local_file=local_out, remote_folder='Data', remote_file=output1)
    faasr_log(f"validate_vwc_data: validated and uploaded {output1} with {len(df)} rows")
