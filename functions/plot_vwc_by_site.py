def plot_vwc_by_site(folder: str, input1: str, output1: str, output2: str, output3: str, output4: str) -> None:
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import tempfile
    import os

    local_in = os.path.join(tempfile.gettempdir(), input1)
    faasr_get_file(local_file=local_in, remote_folder=folder, remote_file=input1)

    df = pd.read_csv(local_in)
    df['Date'] = pd.to_datetime(df['Date'])

    outputs = {1: output1, 2: output2, 3: output3, 4: output4}

    for site_num, out_filename in outputs.items():
        site_df = df[df['Site'] == site_num]
        if site_df.empty:
            msg = f"plot_vwc_by_site: no data for Site {site_num}"
            faasr_log(msg)
            raise ValueError(msg)

        fig, ax = plt.subplots()
        for depth, depth_df in site_df.groupby('Depth (in)'):
            depth_df = depth_df.sort_values('Date')
            ax.plot(depth_df['Date'], depth_df['Volumetric Water content (cm3/cm3)'], label=f'{depth} in')

        ax.set_xlabel('Date')
        ax.set_ylabel('Volumetric Water content (cm3/cm3)')
        ax.set_title(f'Site {site_num} VWC by Depth')
        ax.legend(title='Depth (in)')
        fig.autofmt_xdate()

        local_out = os.path.join(tempfile.gettempdir(), out_filename)
        fig.savefig(local_out)
        plt.close(fig)

        faasr_put_file(local_file=local_out, remote_folder=folder, remote_file=out_filename)
        faasr_log(f"plot_vwc_by_site: uploaded {out_filename}")
