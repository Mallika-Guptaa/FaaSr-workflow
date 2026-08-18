import os
import json
import re
from typing import List, Optional, Tuple

import pandas as pd
from pandas.api.types import is_numeric_dtype


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

    # Avoid converting pure numeric series unless explicitly allowed (name suggests datetime)
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
        parse_ratio = 0.0

        # If already datetime-like dtype
        if pd.api.types.is_datetime64_any_dtype(ser):
            non_na = ser.notna().sum()
            total = len(ser)
            parse_ratio = float(non_na) / float(total) if total else 0.0
            score = parse_ratio + name_weight + 0.1  # small bonus for dtype
            if parse_ratio >= 0.5:  # require at least half non-null datetimes
                candidates.append((col, score))
            continue

        # Otherwise, attempt to parse; allow numeric parsing only if name suggests datetime
        attempt_numeric = name_weight > 0.0
        parse_ratio = _can_parse_datetime(ser, attempt_numeric=attempt_numeric)
        if parse_ratio >= 0.7 or (parse_ratio >= 0.5 and name_weight >= 0.1):
            score = parse_ratio + name_weight
            candidates.append((col, score))

    if not candidates:
        return None

    # Select best-scoring candidate; in tie, keep first occurrence order
    candidates.sort(key=lambda x: x[1], reverse=True)
    best_col = candidates[0][0]
    return best_col


def _is_excluded_name(col: str) -> bool:
    """Heuristic exclusion for IDs, coordinates, codes, and time components."""
    name = col.strip().lower()
    # Use regex word boundaries where appropriate to avoid substrings like 'latest'
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
    # Try converting object-like to numeric
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        s = series.dropna()
        if s.empty:
            return False, 0.0
        conv = pd.to_numeric(s, errors="coerce")
        ratio = float(conv.notna().sum()) / float(len(s))
        return ratio >= 0.9, ratio
    return False, 0.0


def read_and_identify_weather_variables(folder: str, input1: str, output1: str) -> None:
    """Read a CSV from S3 (via FaaSr), detect a datetime column and weather variable columns, and
    write a JSON manifest back to the same folder.

    Parameters:
        folder: Remote folder/prefix in the object store.
        input1: Remote CSV filename (e.g., 'weatherData.csv').
        output1: Remote JSON filename for the manifest (e.g., 'weather_variable_manifest.json').
    """
    try:
        faasr_log(f"Starting read_and_identify_weather_variables with folder='{folder}', input='{input1}'.")

        local_input = "local_" + os.path.basename(input1)
        local_output = "local_" + os.path.basename(output1)

        # Retrieve input CSV
        faasr_get_file(local_file=local_input, remote_folder=folder, remote_file=input1)
        if not os.path.isfile(local_input):
            msg = f"Input file not found after faasr_get_file: {local_input}"
            faasr_log(msg)
            raise FileNotFoundError(msg)

        # Read CSV with robust encoding handling
        try:
            df = pd.read_csv(local_input, low_memory=False)
        except UnicodeDecodeError:
            df = pd.read_csv(local_input, low_memory=False, encoding="latin1")

        if df.shape[1] == 0:
            faasr_log("CSV has no columns; generating empty manifest.")
            manifest = {"datetime_column": None, "variable_columns": [], "excluded_columns": []}
            with open(local_output, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            faasr_put_file(local_file=local_output, remote_folder=folder, remote_file=output1)
            faasr_log("Completed with empty manifest due to no columns.")
            return

        # Detect datetime column
        datetime_col = _detect_datetime_column(df)

        # Identify variable columns
        variable_cols: List[str] = []
        excluded_cols: List[str] = []

        for col in list(df.columns):
            if col == datetime_col:
                continue

            is_excl = _is_excluded_name(col)
            is_num, num_ratio = _infer_numeric(df[col])

            if is_num and not is_excl:
                # Positive hint can help keep borderline numeric
                if num_ratio >= 0.6 or _is_weather_like_name(col):
                    variable_cols.append(col)
                else:
                    excluded_cols.append(col)
            else:
                excluded_cols.append(col)

        # Preserve order and uniqueness
        seen = set()
        variable_cols = [c for c in variable_cols if not (c in seen or seen.add(c))]

        # Build manifest
        manifest = {
            "datetime_column": datetime_col if datetime_col is not None else None,
            "variable_columns": variable_cols,
            "excluded_columns": [c for c in df.columns if c != datetime_col and c not in variable_cols],
        }

        with open(local_output, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Put manifest back to remote folder
        faasr_put_file(local_file=local_output, remote_folder=folder, remote_file=output1)

        faasr_log(
            "Detection complete: datetime_column='{}', variables={} ({} vars).".format(
                manifest["datetime_column"], ", ".join(variable_cols) if variable_cols else "none", len(variable_cols)
            )
        )
    except Exception as e:
        faasr_log(f"Error in read_and_identify_weather_variables: {e}")
        raise