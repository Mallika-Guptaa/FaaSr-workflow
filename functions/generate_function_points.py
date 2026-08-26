def generate_function_points(folder: str, output1: str) -> None:
    """Generate N 2D points and upload to SimpleFunctionPlot folder."""
    
    # Use random.Random(42) to choose N uniformly between 10 and 20 inclusive
    import random
    rng = random.Random(42)
    n_points = rng.randint(10, 20)
    
    # Generate N sorted x-values spanning 0 to 10 (inclusive), evenly spaced
    x_values = [0.0] * n_points
    # Sort them as we fill them (they will be 0 to (n-1)/((n-1))) which is in ascending order
    
    if n_points > 1:
        step = 10.0 / (n_points - 1)
        for i in range(n_points):
            x_values[i] = round(i * step, 6)
    else:
        x_values[0] = 5.0
    
    # Compute y = 2*x + 3 for each x
    y_values = [2.0 * x_val + 3.0 for x_val in x_values]
    
    # Write to local CSV file with header row x,y followed by N rows of values
    csv_content = "x,y\n"
    for xi, yi in zip(x_values, y_values):
        csv_content += "{:.6f},{:.6f}\n".format(xi, yi)
    
    local_file_path = output1
    with open(local_file_path, 'w') as f:
        f.write(csv_content)
    
    # Ensure the local file exists before calling faasr_put_file
    import os
    assert os.path.exists(local_file_path), "Local file {} must exist before upload".format(
        local_file_path)
    
    # Upload to SimpleFunctionPlot folder
    faasr_put_file(local_file=output1, remote_folder=folder, remote_file=output1)
