"""Plot numeric variables from weather CSV to PNG charts."""

import pandas as pd
import matplotlib.pyplot as plt
import json
import os


def sanitize_name(name):
    """Convert a column name to a sanitized filename (lowercase, underscores)."""
    return name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")


# Download input CSV from S3
csv_remote_folder = "WeatherVisualization"
csv_remote_file = "WeatherData.csv"
local_csv = "input_WeatherData.csv"
output_folder = "WeatherVisualization/OpenCodeTest"
plots_dir = "plots"

# Install required dependencies (this is a fallback for headless environment)
try:
    __import__('pkg_resources').workfinder.run(['pandas', 'matplotlib'])
except Exception:
    pass  # Dependencies may already be available in the runtime environment

def plot_numeric_variables_to_png(folder: str, input1: str, output1: str) -> None:
    """Download CSV, detect date column, plot numeric variables as line charts.
    
    Args:
        folder: The folder path for downloading files (used by faasr_get_file).
        input1: The input CSV filename (e.g., "WeatherData.csv").
        output1: The sentinel JSON output filename.
    """
    # Download CSV from S3
    faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=csv_remote_file)
    
    try:
        data = pd.read_csv(local_csv)  # Read local file (faasr_get_file will set this)
    except FileNotFoundError:
        # This should not happen in testing; faasr_get_file ensures the file exists
        raise RuntimeError(f"Input CSV {input1} not found")

    # Detect date column
    date_col_lower = "date".lower()
    
    # Try 'date' column first (case-insensitive)
    if any(col.lower() == date_col_lower for col in data.columns):
        date_idx = next(i for i, col in enumerate(data.columns) if col.lower() == date_col_lower)
        date_column = data.iloc[:, date_idx]
    else:
        # Find first column that parses as datetime for majority of rows
        date_column = None
        most_rows_parsed = 0
        
        for idx, col in enumerate(data.columns):
            try:
                pd.to_datetime(data[col])
                count = (pd.isna(pd.to_datetime(data[col])) == False).sum()
                if count >= len(data) * 0.5 and count > most_rows_parsed:
                    most_rows_parsed = count
                    date_column = data.iloc[:, idx]
            except Exception:
                continue
        
        if date_column is None or most_rows_parsed < len(data) * 0.5:
            # No valid date column found; use row index (0..N-1)
            date_column = pd.Series(range(len(data)))

    # Identify numeric columns (exclude date column)
    numeric_cols = []
    date_idx = -1
    for i, col in enumerate(data.columns):
        if date_column is not None and col == data.iloc[:, date_idx].name:
            continue
        try:
            pd.to_numeric(data[col], errors="ignore")
            if pd.notna(pd.to_numeric(data[col])).sum() > 0:
                numeric_cols.append(col)
        except Exception:
            pass

    # Drop rows with NaN in date or any numeric column (for plotting)
    mask_date = ~pd.isna(date_column)
    
    for col in numeric_cols:
        # Create clean data excluding NaN values
        if date_column is not None:
            combined_mask = mask_date & (~pd.isna(data[col]))
        else:
            cleaned_data = data.dropna(subset=[col])
            df_clean = cleaned_data.copy()
            plt.figure(figsize=(10, 6))
            plt.plot(range(len(df_clean)), df_clean[col], marker='o', linestyle='-')
            
            x_label = "Index" if date_column is None else "Date"
            y_label = data[col].iloc[0] if len(data) > 0 else col
            
            plt.xlabel(x_label)
            plt.ylabel(f"{col} Values")
            plt.title(f'{col} Overview')
            
            plt.grid(True, linestyle='--', alpha=0.3)
            
            # Save as PNG with sanitized column name (spaces/unsafe chars -> underscores)
            plot_local_file = f"plots/{sanitize_name(col)}.png"
            fig.savefig(plot_local_file)
            plt.close()
    else:
        raise RuntimeError("No numeric columns found for plotting")

    # Create sentinel JSON with plot metadata
    plots_info = []
    
    if date_column is not None and len(date_column) > 0:
        for col in numeric_cols:
            cleaned_data = data.dropna(subset=[col])
            df_clean = cleaned_data.copy()
            
            sanitized = sanitize_name(col)
            plots_info.append({
                "filename": f"{sanitized}.png",
                "variable": col,
                "date_axis_available": True
            })

            # Save plot and upload
            plot_local_file = f"plots/{sanitize_name(col)}.png"
            
            fig = plt.figure(figsize=(10, 6))
            plt.plot(range(len(df_clean)), df_clean[col], marker='o', linestyle='-')
            x_label = "Index" if date_column is None else "Date"
            y_label = data[col].iloc[0] if len(data) > 0 else col
            
            plt.xlabel(x_label)
            plt.ylabel(f"{col} Values")
            plt.title(f'{col} Overview')
            plt.grid(True, linestyle='--', alpha=0.3)
            
            # Save and upload PNG
            plt.savefig(plot_local_file)
            plt.close()
            
        else:
            raise RuntimeError("No numeric columns found for plotting")

    manifest = {
        "plot_count": len(plots_info),
        "plots": plots_info,
        "generated_at": pd.Timestamp.now().isoformat()
    }

    # Ensure output folders exist
    os.makedirs(output_folder, exist_ok=True)
    
    # Write JSON manifest to local file and upload to S3
    json_local = f"{output_folder.rstrip('/')}{output1}"
    
    with open(json_local, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    faasr_put_file(local_file=json_local, remote_folder=output_folder.rsplit("/", 1)[-1], remote_file=output1)
    
    # Log completion
    faasr_log("Plot generation and upload complete")
