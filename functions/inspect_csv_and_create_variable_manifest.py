"""Step 1: Download and inspect the weather CSV and create variable manifest."""


def inspect_csv_and_create_variable_manifest(folder: str, input1: str, output1: str) -> None:
    """
    Download WeatherVisualization/WeatherData.csv, inspect it to identify
    (a) a single date column chosen as a column that can be reliably parsed
    as datetimes, and (b) the set of numeric weather variables.

    Create a JSON manifest containing at least: 'date_column' (string or null)
    and 'numeric_variables' (array of column names). Upload the manifest to
    WeatherVisualization/OpenCodeTest/weather_variable_manifest.json using
    faasr_put_file.
    """
    
    # Step 1: Download the CSV from S3
    local_csv_path = "weather_data.csv"
    remote_folder = folder
    remote_input = input1
    
    faasr_log(f"Downloading {remote_input} from {remote_folder}")
    faasr_get_file(local_file=local_csv_path, remote_folder=remote_folder, remote_file=remote_input)

    # Step 2: Load and parse the CSV to inspect columns
    import pandas as pd
    
    df = pd.read_csv(local_csv_path)

    # Step 3: Find date column - prefer names like 'date', 'datetime', 'time', 'timestamp'
    date_prefixed_names = ['date', 'datetime', 'time', 'timestamp']
    
    def is_date_column(column_name: str, df: pd.DataFrame) -> bool:
        """Check if a column contains datetime values."""
        sample = df[column_name].dropna().head(20).tolist()
        if len(sample) < 3 or len(sample) == 0:
            return False
        
        date_like = any(p in column_name.lower() for p in ['date', 'datetime', 'time'])
        
        # Try to convert sample values to datetime - check how many succeed
        success_count = 0
        for val in sample:
            try:
                pd.to_datetime(val)
                success_count += 1
            except (ValueError, TypeError):
                continue
        
        # At least 50% should parse as valid datetime for this to be our date column
        total = len([v for v in sample if not pd.isna(v)])
        return success_count / total >= 0.5 if total > 0 else False
    
    # First, look for columns with dates in their name that could be the main date column
    for col_name in df.columns:
        col_lower = col_name.lower()
        if any(d in col_lower for d in ['date', 'datetime', 'time']):
            if is_date_column(col_name, df):
                date_column = col_name
                break
    
    # If we didn't find one by name, look for the most reliable date column overall
    if date_column is None:
        best_score = -1
        for col in df.columns:
            sample = df[col].dropna().head(20).tolist()
            success = 0
            for v in sample:
                try:
                    pd.to_datetime(v)
                    success += 1
                except (ValueError, TypeError):
                    pass
            
            total = len([v for v in sample if not pd.isna(v)])
            if total > 0 and success / total >= 0.5:
                score = success / total
                if score > best_score:
                    best_score = score
                    date_column = col
    
    # Step 4: Find numeric variables - columns that are numeric after coercion
    # excludes the date column if found
    
    numeric_cols = []
    
    for col in df.columns:
        if date_column and col == date_column:
            continue
        
        try:
            sample_vals = df[col].dropna().head(20).tolist()
            
            def try_to_float(v):
                """Try to convert a value to float, return if successful."""
                try:
                    float(v if not pd.isna(v) else 0.0)
                    return True
                except (ValueError, TypeError):
                    return False
            
            count = sum(try_to_float(v) for v in sample_vals)
            total_with_value = sum(1 for v in sample_vals if not pd.isna(v))
            
            if total_with_value > 5 and count / total_with_value >= 0.5:
                numeric_cols.append(col)
        
        except Exception:
            continue
    
    # Create the manifest
    manifest = {
        "date_column": date_column,
        "numeric_variables": numeric_cols
    }

    local_manifest_path = "weather_variable_manifest.json"
    
    faasr_log(f"Date column found: {date_column}")
    faasr_log(f"Numeric variables identified: {numeric_cols}")
    
    # Step 5: Upload the manifest to S3
    faasr_put_file(local_file=local_manifest_path, remote_folder=folder, remote_file=output1)
