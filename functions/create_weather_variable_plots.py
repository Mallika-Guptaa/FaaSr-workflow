import os
import json
from typing import List, Optional, Tuple

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Ensure headless plotting
import matplotlib.pyplot as plt


def _resolve_case_insensitive_remote_file(folder: str, desired_name: str) -> Optional[str]:
    """Return the exact-cased remote filename within folder matching desired_name, case-insensitively."""
    try:
        names = faasr_get_folder_list(prefix=folder)
    except Exception as e:
        faasr_log(f"Failed to list remote folder '{folder}': {e}")
        raise

    if not names:
        return None

    desired_base = os.path.basename(desired_name).lower()
    matches: List[str] = []
    for key in names:
        base = key.rsplit("/", 1)[-1]
        if base.lower() == desired_base:
            matches.append(base)

    if not matches:
        return None

    matches.sort()
    return matches[0]


def _sanitize_filename(name: str) -> str:
    safe = []
    for ch in name:
        if ch.isalnum():
            safe.append(ch)
        elif ch in (" ", "-", "_", "."):
            safe.append("_")
        else:
            safe.append("_")
    out = "".join(safe)
    # Collapse multiple underscores
    while "__" in out:
        out = out.replace("__", "_")
    out = out.strip("._ ")
    return out or "variable"


def _coerce_numeric(series: pd.Series) -> pd.Series:
    try:
        return pd.to_numeric(series, errors="coerce")
    except Exception:
        return pd.to_numeric(series.astype(str), errors="coerce")


def _prepare_datetime(df: pd.DataFrame, datetime_col: Optional[str]) -> Optional[pd.Series]:
    if datetime_col is None:
        return None
    if datetime_col not in df.columns:
        return None
    try:
        dt = pd.to_datetime(df[datetime_col], errors="coerce", infer_datetime_format=True, utc=False)
        return dt
    except Exception:
        return None


def create_weather_variable_plots(folder: str, input1: str, input2: str, output1: str) -> None:
    """Generate per-variable plots from a weather CSV using manifest guidance and upload PNGs and a sentinel.

    Parameters:
        folder: Remote folder/prefix in the object store (e.g., 'weatherVisualization').
        input1: Expected remote CSV filename (e.g., 'WeatherData.csv'); lookup is case-insensitive.
        input2: Remote manifest filename from the identification step (e.g., 'weather_variable_manifest.json').
        output1: Remote sentinel filename to write (e.g., 'plots_generation_done.txt').
    """
    try:
        faasr_log(
            f"Starting create_weather_variable_plots with folder='{folder}', csv='{input1}', manifest='{input2}', output='{output1}'."
        )

        # Retrieve manifest
        local_manifest = os.path.basename(input2)
        faasr_get_file(local_file=local_manifest, remote_folder=folder, remote_file=input2)
        if not os.path.isfile(local_manifest):
            msg = f"Manifest not found after faasr_get_file: {local_manifest}"
            faasr_log(msg)
            raise FileNotFoundError(msg)

        with open(local_manifest, "r", encoding="utf-8") as f:
            try:
                manifest = json.load(f)
            except json.JSONDecodeError as je:
                faasr_log(f"Failed to parse manifest JSON: {je}")
                raise

        datetime_col = manifest.get("datetime_column")
        variable_columns: List[str] = manifest.get("variable_columns") or []

        # Retrieve CSV: attempt exact-case, then case-insensitive discovery within folder
        resolved_remote_csv = os.path.basename(input1)
        local_csv = "local_" + resolved_remote_csv
        try:
            faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=resolved_remote_csv)
        except Exception as ge:
            faasr_log(f"Exact-case CSV retrieval failed or unavailable ({ge}); attempting case-insensitive discovery.")

        if not os.path.isfile(local_csv):
            match = _resolve_case_insensitive_remote_file(folder, resolved_remote_csv)
            if match is None:
                msg = (
                    f"CSV '{resolved_remote_csv}' not found exactly and no case-insensitive match present in folder '{folder}'."
                )
                faasr_log(msg)
                raise FileNotFoundError(msg)
            resolved_remote_csv = match
            local_csv = "local_" + resolved_remote_csv
            faasr_log(f"Resolved case-insensitive CSV match: '{resolved_remote_csv}'. Downloading...")
            faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=resolved_remote_csv)

        if not os.path.isfile(local_csv):
            msg = f"Input CSV not found after retrieval attempts: {local_csv}"
            faasr_log(msg)
            raise FileNotFoundError(msg)

        # Read CSV robustly
        try:
            df = pd.read_csv(local_csv, low_memory=False)
        except UnicodeDecodeError:
            df = pd.read_csv(local_csv, low_memory=False, encoding="latin1")

        if df.shape[0] == 0 or df.shape[1] == 0:
            faasr_log("CSV is empty or has no columns; no plots will be generated.")
            # Still write and upload sentinel indicating completion with zero plots
            local_sentinel = os.path.basename(output1)
            with open(local_sentinel, "w", encoding="utf-8") as f:
                f.write("No data available to plot.\n")
            faasr_put_file(local_file=local_sentinel, remote_folder=folder, remote_file=output1)
            faasr_log(f"Sentinel '{output1}' uploaded (no plots).")
            return

        # Prepare datetime series if available
        dt_series = _prepare_datetime(df, datetime_col)
        if datetime_col and dt_series is not None and dt_series.notna().sum() > 0:
            faasr_log(f"Using datetime column for x-axis: '{datetime_col}'.")
        elif datetime_col:
            faasr_log(f"Manifest datetime column '{datetime_col}' is unavailable or unparsable; falling back to index for x-axis.")
            dt_series = None
        else:
            faasr_log("No datetime column provided by manifest; using index for x-axis.")

        # Validate variable columns exist
        available_vars = [c for c in variable_columns if c in df.columns]
        missing_vars = [c for c in variable_columns if c not in df.columns]
        if missing_vars:
            faasr_log(f"Warning: {len(missing_vars)} variable(s) listed in manifest not found in CSV: {missing_vars[:5]}{'...' if len(missing_vars) > 5 else ''}")

        if not available_vars:
            faasr_log("No valid variable columns available to plot after checking CSV; proceeding to write sentinel only.")
            local_sentinel = os.path.basename(output1)
            with open(local_sentinel, "w", encoding="utf-8") as f:
                f.write("No variable columns to plot.\n")
            faasr_put_file(local_file=local_sentinel, remote_folder=folder, remote_file=output1)
            faasr_log(f"Sentinel '{output1}' uploaded (no plots).")
            return

        # Plot each variable
        used_names = set()
        generated_pngs: List[str] = []
        for var in available_vars:
            y = _coerce_numeric(df[var])
            if dt_series is not None:
                x = dt_series
            else:
                x = pd.RangeIndex(start=0, stop=len(df), step=1)

            # Drop rows where x or y is NaN
            mask = pd.notna(x) & pd.notna(y)
            x_clean = x[mask]
            y_clean = y[mask]

            if hasattr(x_clean, 'reset_index'):
                try:
                    x_clean = x_clean.reset_index(drop=True)
                except Exception:
                    pass
            if hasattr(y_clean, 'reset_index'):
                try:
                    y_clean = y_clean.reset_index(drop=True)
                except Exception:
                    pass

            if len(y_clean) == 0:
                faasr_log(f"Skipping variable '{var}' — no numeric data after cleaning.")
                continue

            # If datetime, sort by x for cleaner plots
            if dt_series is not None:
                try:
                    order = pd.Series(x_clean).argsort(kind='mergesort')
                    x_plot = pd.Series(x_clean).iloc[order].values
                    y_plot = pd.Series(y_clean).iloc[order].values
                except Exception:
                    x_plot = x_clean
                    y_plot = y_clean
            else:
                x_plot = x_clean
                y_plot = y_clean

            # Determine filename, ensuring uniqueness after sanitization
            base_name = _sanitize_filename(var)
            out_name = f"{base_name}.png"
            if out_name in used_names:
                suffix = 1
                while True:
                    cand = f"{base_name}_{suffix}.png"
                    if cand not in used_names:
                        out_name = cand
                        break
                    suffix += 1
            used_names.add(out_name)

            # Create plot
            plt.figure(figsize=(10, 6))
            try:
                plt.plot(x_plot, y_plot, linewidth=1.5)
            except Exception:
                # Fallback to scatter if plotting fails
                plt.scatter(x_plot, y_plot, s=6)
            plt.title(var)
            if dt_series is not None:
                plt.xlabel(datetime_col if datetime_col else "Index")
            else:
                plt.xlabel("Index")
            plt.ylabel(var)
            plt.grid(True, linestyle='--', linewidth=0.5, alpha=0.6)
            plt.tight_layout()

            # Save locally and upload
            plt.savefig(out_name, dpi=150)
            plt.close()

            try:
                faasr_put_file(local_file=out_name, remote_folder=folder, remote_file=out_name)
                generated_pngs.append(out_name)
            except Exception as ue:
                faasr_log(f"Failed to upload plot '{out_name}': {ue}")
                raise

        # Write and upload sentinel regardless of how many plots were generated
        local_sentinel = os.path.basename(output1)
        with open(local_sentinel, "w", encoding="utf-8") as f:
            f.write("Plot generation completed.\n")
            f.write(f"Total plots: {len(generated_pngs)}\n")
        faasr_put_file(local_file=local_sentinel, remote_folder=folder, remote_file=output1)

        faasr_log(f"Generated and uploaded {len(generated_pngs)} plot(s). Sentinel '{output1}' uploaded.")

    except Exception as e:
        faasr_log(f"Error in create_weather_variable_plots: {e}")
        raise