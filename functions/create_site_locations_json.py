import json
import os
import tempfile


def create_site_locations_json(folder: str, output1: str) -> None:
    site_locations = {
        "1": {"latitude": 44.158669, "longitude": -121.394525, "dms": {"latitude": "44°9'31.21\"N", "longitude": "121°23'40.29\"W"}},
        "2": {"latitude": 44.157778, "longitude": -121.395125, "dms": {"latitude": "44°9'28.00\"N", "longitude": "121°23'42.45\"W"}},
        "3": {"latitude": 44.158756, "longitude": -121.400275, "dms": {"latitude": "44°9'31.52\"N", "longitude": "121°24'0.99\"W"}},
        "4": {"latitude": 44.159150, "longitude": -121.402769, "dms": {"latitude": "44°9'32.94\"N", "longitude": "121°24'9.97\"W"}},
    }

    local_json = os.path.join(tempfile.gettempdir(), output1)
    with open(local_json, "w") as f:
        f.write(json.dumps(site_locations, indent=2))

    faasr_put_file(local_file=local_json, remote_folder=folder, remote_file=output1)
    faasr_log(f"Uploaded {output1} with GPS coordinates for 4 sites to folder {folder}")
