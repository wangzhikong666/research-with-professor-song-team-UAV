"""Summarize fixed-ray repeatability, preserving invalid samples and accuracy limits."""
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np


def describe(values):
    values=np.asarray(values,dtype=float)
    if not len(values):
        return {'samples':0,'mean':None,'sample_std':None,'min':None,'max':None}
    return {'samples':int(len(values)),'mean':float(np.mean(values)),
            'sample_std':float(np.std(values,ddof=1)) if len(values)>1 else None,
            'min':float(values.min()),'max':float(values.max())}


def longest_run(values):
    longest=current=0
    for value in values:
        current=current+1 if value else 0
        longest=max(longest,current)
    return longest


def summarize(rows,metadata):
    good=[row for row in rows if row['valid']]
    indices=np.array([row['frame_index'] for row in rows],dtype=np.int64)
    timestamps=np.array([row['device_timestamp_us'] for row in rows],dtype=np.int64)
    duration=(timestamps[-1]-timestamps[0])/1e6 if len(rows)>1 else None
    xyz=np.array([row['position_m'] for row in good],dtype=float).reshape((-1,3))*1000
    return {'source':'REAL_CAMERA','complete':len(rows)==metadata['requested_frames'],
            'requested_frames':metadata['requested_frames'],'recorded_frames':len(rows),
            'accepted_frames':len(good),'rejected_frames':len(rows)-len(good),
            'rejected_fraction':(len(rows)-len(good))/len(rows) if rows else None,
            'zero_depth_frames':sum(row['raw_depth']==0 for row in rows),
            'longest_rejected_run_frames':longest_run(not r['valid'] for r in rows),
            'longest_zero_depth_run_frames':longest_run(r['raw_depth']==0 for r in rows),
            'all_zero_image_frames':sum(row['frame_all_zero'] for row in rows),
            'reasons':dict(Counter(row['reason'] for row in rows)),
            'device_duration_seconds':duration,
            'received_fps':(len(rows)-1)/duration if duration and duration>0 else None,
            'index_gap_events':int(np.count_nonzero(np.diff(indices)>1)),
            'missing_indices':int(np.maximum(np.diff(indices)-1,0).sum()),
            'nonmonotonic_index_events':int(np.count_nonzero(np.diff(indices)<=0)),
            'nonmonotonic_timestamp_events':int(np.count_nonzero(np.diff(timestamps)<=0)),
            'pixel_uv':metadata['pixel_uv'],'coordinate_frame':metadata['coordinate_frame'],
            'xyz_mm':{axis:describe(xyz[:,i]) for i,axis in enumerate('XYZ')},
            'statistics':'Sample standard deviation (ddof=1); rejected frames excluded from XYZ statistics',
            'interpretation':'Fixed image ray under user-confirmed static scene; X and Y changes derive from Z through the same SDK model, not independent lateral accuracy tests',
            'reference_point':metadata['reference_point'],
            'geometry_verified':False,'physical_accuracy_verified':False,
            'geometry_status':metadata['geometry_status'],
            'absolute_accuracy_reference':'Deferred by user; no independent physical reference used'}


def analyze(directory):
    metadata=json.loads((directory/'session.json').read_text())
    rows=[json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
    summary=summarize(rows,metadata)
    patches=np.load(directory/'point_raw_patches.npy',allow_pickle=False)
    if patches.dtype!=np.uint16 or patches.shape!=(len(rows),5,5):
        raise ValueError('Raw patch shape or dtype mismatch')
    if not np.array_equal(patches[:,2,2],[r['raw_depth'] for r in rows]):
        raise ValueError('Saved raw pixel values do not match sample records')
    for row in rows:
        if row['valid']:
            if abs(row['position_m'][2]*1000-row['raw_depth'])>1e-5:
                raise ValueError('SDK Z and raw depth mismatch')
        elif row['position_m'] is not None:
            raise ValueError('Rejected sample has a position')
    summary['saved_raw_patches_verified']=True
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(9,7),sharex=True,constrained_layout=True)
    stamps=np.array([r['device_timestamp_us'] for r in rows],dtype=float)
    seconds=(stamps-stamps[0])/1e6 if len(stamps) else stamps
    accepted=np.array([r['valid'] for r in rows],dtype=bool)
    for i,axis in enumerate('XYZ'):
        values=np.array([r['position_m'][i]*1000 if r['valid'] else np.nan for r in rows])
        mean=summary['xyz_mm'][axis]['mean']
        axes[i].plot(seconds,values-mean if mean is not None else values,'.-',ms=3,lw=.6)
        axes[i].set_ylabel(f'{axis} - mean (mm)'); axes[i].grid(alpha=.3)
        if len(seconds) and (~accepted).any():
            axes[i].plot(seconds[~accepted],np.zeros((~accepted).sum()),'rx',label='Rejected (marker at zero, not a measurement)')
            axes[i].legend(fontsize=7)
    axes[-1].set_xlabel('Device time from first recorded frame (s)')
    fig.suptitle('REAL CAMERA: fixed-pixel SDK XYZ repeatability\nNo absolute accuracy reference; geometric calibration remains unverified',fontsize=10)
    fig.savefig(directory/'repeatability.png',dpi=150); plt.close(fig)
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    analyze(parser.parse_args().directory)
