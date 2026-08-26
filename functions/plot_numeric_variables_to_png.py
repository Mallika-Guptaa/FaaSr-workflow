"""Plot numeric variables from weather CSV to PNG charts."""

import pandas as pd
import matplotlib.pyplot as plt
import json
import os


def sanitize_name(name):
    """Convert a column name to a sanitized filename (lowercase, underscores)."""
    return name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")


# Global variables set by caller in stubs/execution
csv_remote_folder = "WeatherVisualization"
csv_remote_file = "WeatherData.csv"
local_csv = "input_WeatherData.csv"
output_folder = "WeatherVisualization/OpenCodeTest"
plots_dir = "plots"


def plot_numeric_variables_to_png(folder: str, input1: str, output1: str) -> None:
    """Download CSV, detect date column, plot numeric variables as line charts.
    
    Args:
        folder: The folder path for downloading files (used by faasr_get_file).
        input1: The input CSV filename (e.g., "WeatherData.csv").
        output1: The sentinel JSON output filename.
    """
    # Download CSV from S3
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)
    
    try:
        data = pd.read_csv(local_csv)  # Read local file (faasr_get_file will set this)
    except FileNotFoundError:
        raise RuntimeError(f"Input CSV {input1} not found")

    # Detect date column (if any)
    date_col_lower = "date".lower()
    
    # Try 'date' column first (case-insensitive)
    if any(col.lower() == date_col_lower for col in data.columns):
        date_idx = next(i for i, col in enumerate(data.columns) if col.lower() == date_col_lower)
        date_column = data.iloc[:, date_idx]
    else:
        # No date column found - will use row index as x-axis
        date_column = None

    # Identify numeric columns (exclude date column if present)
    numeric_cols = []
    for i, col in enumerate(data.columns):
        is_date = date_column is not None and col == data.iloc[:, i].name
        try:
            pd.to_numeric(data[col], errors="ignore")
            non_nan_count = ~pd.isna(pd.to_numeric(data[col])).sum()
            if non_nan_count > 0 and not is_date:
                numeric_cols.append(col)
        except Exception:
            pass

    if len(numeric_cols) == 0:
        raise RuntimeError("No numeric columns found for plotting")

    # Determine X-axis variable name for labels
    x_label = "Index" if date_column is None else date_column.name

    # Drop rows with NaN in any numeric column (for all plots)
    drop_cols = []
    for col in data.columns:
        try:
            pd.to_numeric(data[col], errors="ignore")
            non_nan_count = ~pd.isna(pd.to_numeric(data[col])).sum()
            if non_nan_count > 0 and col not in numeric_cols:
                # This column is numeric but doesn't have NaN values, skip it
                pass
            else:
                drop_cols.append(col)
        except Exception:
            pass

    clean_data = data.dropna(subset=drop_cols)

    # Create plot for each numeric column
    fig = plt.figure(figsize=(10, 6))

    for col in numeric_cols:
        cleaned_data = clean_data.copy()
        
        fig = plt.figure(figsize=(10, 6))
        plt.plot(range(len(cleaned_data)), cleaned_data[col], marker='o', linestyle='-')
        
        plt.xlabel(x_label)
        plt.ylabel(f"{col} Values")
        plt.title(f'{col} Overview')
        
        plt.grid(True, linestyle='--', alpha=0.3)
        
        # Save as PNG with sanitized column name (spaces/unsafe chars -> underscores)
        plot_local_file = f"plots/{sanitize_name(col)}.png"
        fig.savefig(plot_local_file)
        plt.close()

    faasr_log("Plot generation complete")

    manifest = {
        "plot_count": len(numeric_cols),
        "plots": [],
        "generated_at": pd.Timestamp.now().isoformat()
    }

    os.makedirs(output_folder, exist_ok=True)
    
    json_path = f"{output_folder}/plots_generation_complete.json"
    
    with open(json_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    faasr_put_file(local_file=json_path, remote_folder="WeatherVisualization", remote_file=output1)
