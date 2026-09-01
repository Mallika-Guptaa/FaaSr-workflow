import os
import tempfile


def upload_plots(folder: str, input1: str, input2: str, input3: str, input4: str,
                 output1: str, output2: str, output3: str, output4: str) -> None:
    pairs = [(input1, output1), (input2, output2), (input3, output3), (input4, output4)]

    for in_name, out_name in pairs:
        local_png = os.path.join(tempfile.gettempdir(), in_name)
        faasr_log(f"Downloading {in_name} from folder {folder}")
        faasr_get_file(local_file=local_png, remote_folder=folder, remote_file=in_name)

        faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=out_name)
        faasr_log(f"Uploaded {out_name} to folder {folder}")

    faasr_log("All four plots uploaded to S3")
