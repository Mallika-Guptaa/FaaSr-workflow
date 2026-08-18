import os
import json
import re
from typing import List, Optional, Tuple

import pandas as pd
from pandas.api.types import is_numeric_dtype
from dateutil import parser as _dateutil_parser  # Imported to satisfy dependency requirements


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
    matches = []
    for key in names:
        base = key.rsplit("/", 1)[-1]
        if base.lower() == desired_base:
            matches.append(base)

    if not matches:
        return None

    # Deterministic choice if multiple
    matches.sort()
    return matches[0]


def read_and_identify_weather_variables(folder: str, input1: str, output1: str) -> None:
    """Discover the CSV case-insensitively, detect datetime and weather variables, and write a manifest.

    Parameters:
        folder: Remote folder/prefix in the object store (e.g., 'weatherVisualization').
        input1: Expected remote CSV filename (e.g., 'WeatherData.csv'); lookup is case-insensitive.
        output1: Remote manifest filename to write (e.g., 'weather_variable_manifest.json').
    """
    try:
        faasr_log(
            f"Starting read_and_identify_weather_variables with folder='{folder}', csv='{input1}', output='{output1}'."
        )

        # Attempt exact-case retrieval first
        local_csv = "local_" + os.path.basename(input1)
        try:
            faasr_get_file(local_file=local_csv, remote_folder=folder, remote_file=input1)
        except Exception as ge:
            # Proceed to case-insensitive resolution on failure
            faasr_log(f"Exact-case retrieval failed or unavailable ({ge}); attempting case-insensitive discovery.")

        resolved_remote_csv = os.path.basename(input1)

        if not os.path.isfile(local_csv):
            # Resolve case-insensitive match within the folder
            match = _resolve_case_insensitive_remote_file(folder, input1)
            if match is None:
                msg = (
                    f"CSV '{input1}' not found exactly and no case-insensitive match present in folder '{folder}'."
                )
                faasr_log(msg)
                raise FileNotFoundError(msg)
            # Retrieve the matched file name
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
            faasr_log("CSV is empty or has no columns; producing empty manifest.")
            manifest = {
                "resolved_input_csv": resolved_remote_csv,
                "datetime_column": None,
                "variable_columns": [],
                "total_variables": 0,
            }
        else:
            # Detect datetime column
            datetime_col: Optional[str] = _detect_datetime_column(df)
            if datetime_col is not None:
                faasr_log(f"Detected datetime column: {datetime_col}.")
            else:
                faasr_log("No datetime column detected.")

            # Select candidate weather variables
            variable_cols: List[str] = _select_variables(df, datetime_col)
            faasr_log(f"Identified {len(variable_cols)} candidate variable(s).")

            manifest = {
                "resolved_input_csv": resolved_remote_csv,
                "datetime_column": datetime_col,
                "variable_columns": variable_cols,
                "total_variables": len(variable_cols),
            }

        # Write manifest locally (use the exact provided output filename)
        local_manifest = os.path.basename(output1)
        with open(local_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Upload manifest back to remote folder
        faasr_put_file(local_file=local_manifest, remote_folder=folder, remote_file=output1)

        faasr_log(
            f"Manifest written with datetime='{manifest.get('datetime_column')}', "
            f"variables={manifest.get('total_variables')} and uploaded as '{output1}'."
        )

    except Exception as e:
        faasr_log(f"Error in read_and_identify_weather_variables: {e}")
        raise