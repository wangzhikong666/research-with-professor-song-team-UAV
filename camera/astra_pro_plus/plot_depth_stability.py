"""Plot recorded real depth timing and repeat readings; does not claim accuracy."""
import argparse
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    folder = args.directory
    config = json.loads((folder/'depth_config.json').read_text())
    with (folder/'depth_frames.csv').open() as file:
        rows = list(csv.DictReader(file))
    if len(rows) < 2 or 'center_raw' not in rows[0]:
        raise RuntimeError('Need at least two frames with central pixel statistics')
    times = np.array([int(r['device_timestamp_us']) for r in rows],dtype=np.int64)
    seconds = (times-times[0])/1e6
    raw = np.array([int(r['center_raw']) for r in rows],dtype=float)
    valid = (raw>0)&(raw>=config['sdk_min_raw'])&(raw<=config['sdk_max_raw'])
    center = np.where(valid,raw*config['millimeters_per_raw_unit'],np.nan)
    roi = config['central_roi']
    fraction = np.array([int(r['roi_valid_pixels']) for r in rows])/(roi['width']*roi['height'])*100
    with plt.rc_context({'font.size':10,'axes.spines.top':False,'axes.spines.right':False}):
        fig, axes = plt.subplots(3,1,figsize=(10,7),sharex=True,layout='constrained')
        axes[0].plot(seconds,center,color='#20639b',lw=0.8)
        axes[0].set_ylabel('Center pixel Z (mm)')
        axes[1].plot(seconds,fraction,color='#d78119',lw=0.8)
        axes[1].set_ylabel('Valid pixels in ROI (%)')
        axes[1].set_ylim(0,102)
        axes[2].plot(seconds[1:],np.diff(times)/1000,color='#267452',lw=0.8)
        axes[2].set_ylabel('Frame interval (ms)')
        axes[2].set_ylim(0,max(40,float(np.diff(times).max()/1000)*1.05))
        axes[2].ticklabel_format(axis='y',style='plain',useOffset=False)
        axes[2].set_xlabel('Device timestamp elapsed (s)')
        for ax in axes:
            ax.grid(alpha=0.2)
            ax.axvspan(0,min(1,seconds[-1]),color='gray',alpha=0.12)
        fig.suptitle('Single-depth stream: recorded surface readings and availability\nUser-reported fixed scene; no reference measurement or absolute-accuracy claim',fontsize=12)
        fig.savefig(folder/'depth_stability.png',dpi=180)
        plt.close(fig)
    print(folder/'depth_stability.png')


if __name__ == '__main__':
    main()
