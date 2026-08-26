"""Generate plot manifest from remote prefix."""

import json
import tempfile
import os


def generate_plot_manifest_from_remote_prefix(folder: str, input1: str, output1: str) -> None:
    """Enumerate PNG files in a folder and create a JSON manifest.

    Args:
        folder: S3 folder prefix to enumerate (e.g., "WeatherVisualization/OpenCodeTest").
        input1: Sentinel file ensuring plot uploads are complete.
        output1: Output filename for the manifest JSON.
    """
    # First, verify the sentinel file exists by attempting to get it
    faasr_get_file(local_file=input1, remote_folder=folder, remote_file=input1)

    # Get a list of all files in the folder
    all_files = faasr_get_folder_list(prefix=folder)

    # Filter to PNG files (case-insensitive extension) and extract basenames
    png_filenames = []
    for full_key in all_files:
        # Extract basename by taking everything after the last slash
        basename = full_key.rsplit("/", 1)[-1]
        if basename.lower().endswith(".png"):
            png_filenames.append(basename)

    # Create manifest object with filenames and total count
    manifest = {
        "filenames": png_filenames,
        "total_plots": len(png_filenames)
    }

    # Serialize to JSON string for upload
    manifest_json = json.dumps(manifest)

    # Write to local temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(manifest_json)
        local_manifest_file = f.name

    # Upload the manifest to S3
    faasr_put_file(local_file=local_manifest_file, remote_folder=folder, remote_file=output1)

    # Clean up temp file
    os.remove(local_manifest_file)

    faasr_log("Plot manifest generated and uploaded successfully")
