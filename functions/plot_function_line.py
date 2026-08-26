def plot_function_line(folder: str, input1: str, output1: str) -> None:
    """Download function_points.csv from SimpleFunctionPlot and create a line plot."""
    
    # Download the CSV file from SimpleFunctionPlot S3 folder
    faasr_get_file(local_file=input1, remote_folder=folder, remote_file=input1)
    
    # Set Agg backend before importing pyplot
    import matplotlib
    matplotlib.use('Agg')
    
    # Import pyplot after setting backend
    import matplotlib.pyplot as plt
    
    # Read x and y columns from the downloaded CSV file
    import csv
    
    x_values = []
    y_values = []
    
    with open(input1, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            x_values.append(float(row['x']))
            y_values.append(float(row['y']))
    
    # Create 2D line plot with markers using x on horizontal and y on vertical axis
    plt.plot(x_values, y_values, 'o-')
    
    # Save the plot locally as function_line_plot.png
    local_png_path = output1
    png_data = plt.savefig(local_png_path)
    plt.clf()
    
    # Ensure the PNG file is successfully created on the local filesystem before uploading
    import os
    assert os.path.exists(local_png_path), "Local PNG file {} must exist before upload".format(
        local_png_path)
    
    # Upload the PNG to SimpleFunctionPlot S3 folder
    faasr_put_file(local_file=output1, remote_folder=folder, remote_file=output1)
