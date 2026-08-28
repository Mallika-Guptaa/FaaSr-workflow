import pandas as pd
import tempfile
import os

def generate_points(folder: str, output1: str) -> None:
    x_values = list(range(20))
    y_values = [2 * x + 3 for x in x_values]
    df = pd.DataFrame({'x': x_values, 'y': y_values})

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        df.to_csv(tmp_path, index=False)
        faasr_log(f"Generated {len(df)} points for y = 2x + 3")
        faasr_put_file(local_file=tmp_path, remote_folder=folder, remote_file=output1)
        faasr_log(f"Uploaded {output1} to {folder}")
    finally:
        os.unlink(tmp_path)
