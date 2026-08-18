import os
import json
from datetime import datetime, timezone


def write_weather_plot_manifest(folder: str, input1: str, output1: str) -> None:
    """Create a manifest of all PNG plots in the current working directory and upload it.

    Parameters:
        folder: Remote folder/prefix in the object store (e.g., 'weatherVisualization').
        input1: Remote sentinel filename indicating plots were generated (e.g., 'plots_generation_done.txt').
        output1: Remote manifest filename to write (e.g., 'weather_plot_manifest.json').
    """
    try:
        faasr_log(
            f"Starting write_weather_plot_manifest with folder='{folder}', sentinel='{input1}', output='{output1}'."
        )

        # Retrieve the sentinel to ensure plotting step completed and to align local mirror state
        local_sentinel = "local_" + os.path.basename(input1)
        faasr_get_file(local_file=local_sentinel, remote_folder=folder, remote_file=input1)
        if not os.path.isfile(local_sentinel):
            msg = f"Sentinel not found after faasr_get_file: {local_sentinel}"
            faasr_log(msg)
            raise FileNotFoundError(msg)

        # Discover PNG files in the current working directory (non-hidden, non-recursive)
        try:
            entries = os.listdir(".")
        except Exception as le:
            faasr_log(f"Failed to list working directory: {le}")
            raise

        pngs = []
        for name in entries:
            # Ignore hidden files and non-files
            if name.startswith('.'):
                continue
            if not os.path.isfile(name):
                continue
            if name.lower().endswith('.png'):
                pngs.append(os.path.basename(name))

        # Sort for deterministic output
        pngs.sort()

        manifest = {
            "plots": pngs,
            "total_plots": len(pngs),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }

        # Write manifest to the exact local filename (no 'local_' prefix)
        local_manifest = os.path.basename(output1)
        if local_manifest != "weather_plot_manifest.json":
            # Enforce spec-mandated filename while still honoring provided output1 for remote
            local_manifest = "weather_plot_manifest.json"
        with open(local_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Upload manifest back to the same remote folder
        faasr_put_file(local_file=local_manifest, remote_folder=folder, remote_file=output1)

        faasr_log(f"Manifest created with {len(pngs)} plot(s) and uploaded as '{output1}'.")
    except Exception as e:
        faasr_log(f"Error in write_weather_plot_manifest: {e}")
        raise