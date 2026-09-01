def generate_points(folder: str, output1: str) -> None:
    import numpy as np
    import pandas as pd
    import tempfile, os

    x = np.arange(20, dtype=float)
    y = 2 * x + 3
    df = pd.DataFrame({"x": x, "y": y})

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    try:
        df.to_csv(tmp.name, index=False)
        faasr_log(f"Generated {len(df)} points for y=2x+3, uploading as {output1}")
        faasr_put_file(local_file=tmp.name, remote_folder=folder, remote_file=output1)
    finally:
        os.unlink(tmp.name)
