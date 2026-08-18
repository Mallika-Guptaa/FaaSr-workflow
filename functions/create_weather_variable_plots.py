import os
import json
import re
from typing import List, Optional, Tuple

import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")  # Ensure headless backend for non-interactive environments
import matplotlib.pyplot as plt


def _likely_datetime_name(col: str) -> float:
    """Return a weight [0, 0.3] indicating how much the name suggests datetime."""
    name = col.strip().lower()
    weight = 0.0
    if any(k in name for k in ("datetime", "timestamp")):
        weight += 0.2
    if "date" in name:
        weight += 0.15
    if "time" in name:
        weight += 0.1
    if any(k in name for k in ("valid", "obs", "observation")):
        weight += 0.05
    return min(weight, 0.3)


def _can_parse_datetime(series: pd.Series, attempt_numeric: bool) -> float:
    """Attempt to parse a Series to datetime and return parse success ratio (0..1).

    attempt_numeric: if False, skip parsing numeric dtypes to avoid misclassifying numeric values as epoch.
    """
    s = series.dropna()
    if s.empty:
        return 0.0

    if not attempt_numeric and is_numeric_dtype(s):
        return 0.0

    try:
        parsed = pd.to_datetime(s, errors="coerce", infer_datetime_format=True, utc=False)
        valid = parsed.notna().sum()
        total = len(s)
        return float(valid) / float(total) if total else 0.0
    except Exception:
        return 0.0


def _detect_datetime_column(df: pd.DataFrame) -> Optional[str]:
    """Detect a suitable datetime column using name heuristics and parse success ratio."""
    candidates: List[Tuple[str, float]] = []  # (col, score)
    for col in df.columns:
        name_weight = _likely_datetime_name(col)
        ser = df[col]

        # If already datetime-like dtype
        if pd.api.types.is_datetime64_any_dtype(ser):
            non_na = ser.notna().sum()
            total = len(ser)
            parse_ratio = float(non_na) / float(total) if total else 0.0
            score = parse_ratio + name_weight + 0.1
            if parse_ratio >= 0.5:
                candidates.append((col, score))
            continue

        attempt_numeric = name_weight > 0.0
        parse_ratio = _can_parse_datetime(ser, attempt_numeric=attempt_numeric)
        if parse_ratio >= 0.7 or (parse_ratio >= 0.5 and name_weight >= 0.1):
            score = parse_ratio + name_weight
            candidates.append((col, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _is_excluded_name(col: str) -> bool:
    """Heuristic exclusion for IDs, coordinates, codes, and time components."""
    name = col.strip().lower()
    patterns = [
        r"\b(id|idx|index|objectid|uid|guid)\b",
        r"\b(station|station_id|stid|stn|site|site_id|provider|source)\b",
        r"\b(wmo|icao|usaf|wban|metar|icao_code|code|type|class|category|status)\b",
        r"\b(lat|latitude|lon|lng|longitude|elev|elevation|altitude)\b",
        r"\b(year|month|day|hour|minute|min|second|sec|week|dow|doy|julian)\b",
        r"\b(flag|qc|quality|units|unit|tz|timezone|utc_offset)\b",
    ]
    for pat in patterns:
        if re.search(pat, name):
            return True
    return False


def _is_weather_like_name(col: str) -> bool:
    """Positive hints for weather variables by name."""
    name = col.strip().lower()
    include_terms = [
        "temp", "temperature", "tmin", "tmax", "tavg", "dew", "dewpoint",
        "humid", "rh", "wind", "gust", "speed", "direction", "dir",
        "precip", "prcp", "rain", "snow", "snwd", "hail",
        "press", "pressure", "slp", "stp", "baro",
        "solar", "radi", "srad", "uv",
        "visibility", "vis", "cloud", "ceiling", "cover",
        "heat_index", "wind_chill",
    ]
    return any(term in name for term in include_terms)


def _infer_numeric(series: pd.Series) -> Tuple[bool, float]:
    """Return (is_numeric, numeric_ratio) where numeric_ratio is fraction convertible to numbers."""
    if is_numeric_dtype(series):
        non_na = series.notna().sum()
        total = len(series)
        ratio = float(non_na) / float(total) if total else 0.0
        return True, ratio
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        s = series.dropna()
        if s.empty:
            return False, 0.0
        conv = pd.to_numeric(s, errors="coerce")
        ratio = float(conv.notna().sum()) / float(len(s))
        return ratio >= 0.9, ratio
    return False, 0.0


def _sanitize_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).strip())
    base = re.sub(r"_+", "_", base).strip("._-")
    return base.lower() or "variable"


def _select_variables(df: pd.DataFrame, datetime_col: Optional[str]) -> List[str]:
    vars_out: List[str] = []
    for col in df.columns:
        if datetime_col is not None and col == datetime_col:
            continue
        if _is_excluded_name(col):
            continue
        is_num, num_ratio = _infer_numeric(df[col])
        if is_num and (num_ratio >= 0.6 or _is_weather_like_name(col)):
            vars_out.append(col)
    # Preserve order & uniqueness
    seen = set()
    uniq = [c for c in vars_out if not (c in seen or seen.add(c))]
    return uniq


def create_weather_variable_plots(folder: str, input1: str, input2: str, output1: str) -> None:
    """Read CSV and optional manifest, generate per-variable PNG line plots, and write a sentinel file.

    Parameters:
        folder: Remote folder/prefix in the object store (e.g., 'weatherVisualization').
        input1: Remote CSV filename (e.g., 'weatherData.csv').
        input2: Remote JSON manifest filename from prior step (e.g., 'weather_variable_manifest.json').
        output1: Remote sentinel filename to write after all plots (e.g., 'plots_generation_done.txt').
    """
    try:
        faasr_log(
            f"Starting create_weather_variable_plots with folder='{folder}', csv='{input1}', manifest='{input2}'."
        )

        local_csv = "local_" + os.path.basename(input1)
        local_manifest = "local_" + os.path.basename(input2)
        local_sentinel = "local_" + os.path.basename(output1)

        # Retrieve CSV
        faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)
        if not os.path.isfile(local_csv):
            msg = f"Input CSV not found after faasr_get_file: {local_csv}"
            faasr_log(msg)
            raise FileNotFoundError(msg)

        # Retrieve manifest if available (optional)
        manifest: Optional[dict] = None
        try:
            faasr_get_file(local_file=local_manifest, remote_folder=folder, remote_file=input2)
            if os.path.isfile(local_manifest):
                with open(local_manifest, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                    faasr_log("Loaded detection manifest for plotting guidance.")
            else:
                faasr_log("Manifest file not present; proceeding with auto-detection.")
        except Exception as me:
            faasr_log(f"Manifest retrieval not available or failed ({me}); proceeding with auto-detection.")
            manifest = None

        # Read CSV robustly
        try:
            df = pd.read_csv(local_csv, low_memory=False)
        except UnicodeDecodeError:
            df = pd.read_csv(local_csv, low_memory=False, encoding="latin1")

        if df.shape[0] == 0 or df.shape[1] == 0:
            faasr_log("CSV is empty or has no columns; no plots will be generated.")
            with open(local_sentinel, "w", encoding="utf-8") as f:
                f.write("Generated 0 plot(s).\n")
            faasr_put_file(local_file=local_sentinel, remote_folder=folder, remote_file=output1)
            return

        # Determine datetime column
        datetime_col: Optional[str] = None
        parsed_datetime: Optional[pd.Series] = None

        man_dt = None
        if manifest and isinstance(manifest, dict):
            man_dt = manifest.get("datetime_column")

        if man_dt and man_dt in df.columns:
            ser = df[man_dt]
            parse_ratio = 0.0
            if pd.api.types.is_datetime64_any_dtype(ser):
                parsed = ser
                non_na = parsed.notna().sum()
                total = len(parsed)
                parse_ratio = float(non_na) / float(total) if total else 0.0
            else:
                parsed = pd.to_datetime(ser, errors="coerce", infer_datetime_format=True)
                parse_ratio = float(parsed.notna().sum()) / float(len(parsed)) if len(parsed) else 0.0
            if parse_ratio >= 0.5:
                datetime_col = man_dt
                parsed_datetime = parsed
                faasr_log(f"Using manifest-specified datetime column: {datetime_col} (parse_ratio={parse_ratio:.2f}).")
            else:
                faasr_log(
                    f"Manifest datetime column '{man_dt}' insufficiently parseable (ratio={parse_ratio:.2f}); auto-detecting."
                )

        if datetime_col is None:
            auto_dt = _detect_datetime_column(df)
            if auto_dt is not None:
                ser = df[auto_dt]
                if pd.api.types.is_datetime64_any_dtype(ser):
                    parsed_datetime = ser
                else:
                    parsed_datetime = pd.to_datetime(ser, errors="coerce", infer_datetime_format=True)
                datetime_col = auto_dt
                faasr_log(f"Auto-detected datetime column: {datetime_col}.")
            else:
                faasr_log("No datetime column detected; will use row index for x-axis.")

        # Determine variable columns
        vars_from_manifest: List[str] = []
        if manifest and isinstance(manifest, dict):
            vcols = manifest.get("variable_columns")
            if isinstance(vcols, list):
                # Keep only existing columns
                vars_from_manifest = [c for c in vcols if c in df.columns]
                if vars_from_manifest:
                    faasr_log(f"Using {len(vars_from_manifest)} variable(s) from manifest.")
                else:
                    faasr_log("No usable variables in manifest; will auto-detect numeric weather variables.")

        if vars_from_manifest:
            variable_cols = vars_from_manifest
        else:
            variable_cols = _select_variables(df, datetime_col)
            faasr_log(f"Auto-detected {len(variable_cols)} variable(s) for plotting.")

        # Prepare x-axis data
        if datetime_col is not None and parsed_datetime is None:
            ser = df[datetime_col]
            if pd.api.types.is_datetime64_any_dtype(ser):
                parsed_datetime = ser
            else:
                parsed_datetime = pd.to_datetime(ser, errors="coerce", infer_datetime_format=True)

        has_datetime = datetime_col is not None and parsed_datetime is not None

        # Apply a readable style
        try:
            plt.style.use("ggplot")
        except Exception:
            pass

        generated = 0
        used_names = set()

        for var in variable_cols:
            try:
                if var not in df.columns:
                    faasr_log(f"Variable '{var}' not found in CSV; skipping.")
                    continue

                y = df[var]
                # Ensure numeric
                if not is_numeric_dtype(y):
                    y = pd.to_numeric(y, errors="coerce")

                if has_datetime:
                    x = parsed_datetime
                    data = pd.DataFrame({"x": x, "y": y})
                    data = data.dropna(subset=["x", "y"])  # require both valid
                else:
                    data = pd.DataFrame({"x": pd.RangeIndex(start=0, stop=len(y), step=1), "y": y})
                    data = data.dropna(subset=["y"])  # drop only invalid y

                if data.shape[0] < 2:
                    faasr_log(f"Variable '{var}' has fewer than 2 valid points; skipping plot.")
                    continue

                fig, ax = plt.subplots(figsize=(10, 5))
                ax.plot(data["x"], data["y"], linewidth=1.6, color="#1f77b4")
                ax.set_title(str(var))
                ax.set_ylabel(str(var))
                ax.set_xlabel(datetime_col if has_datetime else "Index")
                ax.grid(True, linestyle="--", alpha=0.4)

                if has_datetime:
                    fig.autofmt_xdate()

                plt.tight_layout()

                base_name = _sanitize_filename(var) + ".png"
                # Ensure unique filename if collision occurs after sanitization
                remote_png = base_name
                idx = 2
                while remote_png in used_names:
                    remote_png = _sanitize_filename(var) + f"_{idx}.png"
                    idx += 1
                used_names.add(remote_png)

                local_png = f"local_{remote_png}"
                fig.savefig(local_png, dpi=150)
                plt.close(fig)

                faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=remote_png)
                generated += 1
                faasr_log(f"Saved plot for '{var}' as '{remote_png}'.")
            except Exception as pe:
                faasr_log(f"Failed to generate plot for variable '{var}': {pe}")
                # Continue to next variable without raising here
                continue

        # Write sentinel file
        with open(local_sentinel, "w", encoding="utf-8") as f:
            f.write(f"Generated {generated} plot(s).\n")
        faasr_put_file(local_file=local_sentinel, remote_folder=folder, remote_file=output1)
        faasr_log(f"Plot generation complete. Total plots: {generated}. Sentinel '{output1}' written.")

    except Exception as e:
        faasr_log(f"Error in create_weather_variable_plots: {e}")
        raise