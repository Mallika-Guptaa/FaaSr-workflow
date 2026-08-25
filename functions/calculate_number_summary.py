def calculate_number_summary(folder: str, output1: str) -> None:
    """Compute summary statistics for integers 1 through 10.
    
    Args:
        folder: The workflow data directory path (not used since we generate data internally)
        output1: Output filename - should be "number_summary.json"
    
    This is a pure local function that generates integers 1..10 internally,
    computes summary statistics, and saves to JSON.
    """
    # Generate integers 1 through 10
    data = list(range(1, 11))
    
    # Compute summary statistics
    count = len(data)
    total = sum(data)
    mean = total / count
    minimum = min(data)
    maximum = max(data)
    
    # Build the result dictionary
    result = {
        "count": count,
        "sum": total,
        "mean": mean,
        "minimum": minimum,
        "maximum": maximum
    }
    
    # Write to local file in the folder
    import os
    json_path = os.path.join(folder, output1)
    with open(json_path, "w") as f:
        import json
        json.dump(result, f, indent=2)
    
    faasr_log("Summary statistics computed and saved")
