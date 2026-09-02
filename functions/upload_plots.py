import os


def upload_plots(
    folder: str,
    input1: str,
    input2: str,
    input3: str,
    input4: str,
    output1: str,
    output2: str,
    output3: str,
    output4: str,
) -> None:
    plots = [
        (input1, output1),
        (input2, output2),
        (input3, output3),
        (input4, output4),
    ]

    for in_file, out_file in plots:
        local_png = f"upload_tmp_{in_file}"
        faasr_log(f"Downloading {in_file} from folder {folder}")
        faasr_get_file(local_file=local_png, remote_folder=folder, remote_file=in_file)

        if not os.path.exists(local_png) or os.path.getsize(local_png) == 0:
            raise RuntimeError(f"Plot file {in_file} is missing or empty")

        faasr_log(f"Uploading {out_file} to folder {folder}")
        faasr_put_file(local_file=local_png, remote_folder=folder, remote_file=out_file)
        os.remove(local_png)

    faasr_log("upload_plots complete")
