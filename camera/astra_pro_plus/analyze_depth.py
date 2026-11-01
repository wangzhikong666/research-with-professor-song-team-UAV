"""Inspect recorded REAL depth without altering the raw file."""
import argparse
import csv
import json
from pathlib import Path
import cv2
import numpy as np


def statistics(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {'samples': 0}
    median = float(np.median(values))
    return {
        'samples': int(len(values)), 'min': float(values.min()), 'median': median,
        'max': float(values.max()), 'p05': float(np.percentile(values, 5)),
        'p95': float(np.percentile(values, 95)),
        'sample_std': float(values.std(ddof=1)) if len(values) > 1 else None,
        'median_absolute_deviation': float(np.median(np.abs(values-median))),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--warmup-seconds', type=float, default=1.0)
    args = parser.parse_args()
    if args.warmup_seconds < 0:
        parser.error('--warmup-seconds must be nonnegative')
    folder = args.directory
    config = json.loads((folder/'depth_config.json').read_text())
    depth = cv2.imread(str(folder/'depth_raw.pgm'), cv2.IMREAD_UNCHANGED)
    if depth is None or depth.dtype != np.uint16:
        raise RuntimeError('Expected unchanged uint16 raw depth PGM')
    if depth.shape != (config['height'], config['width']):
        raise RuntimeError('Raw dimensions differ from saved configuration')
    valid = (depth > 0) & (depth >= config['sdk_min_raw']) & (depth <= config['sdk_max_raw'])
    with (folder/'depth_frames.csv').open() as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise RuntimeError('No recorded frames')
    sensor = np.array([int(r['device_timestamp_us']) for r in rows], dtype=np.int64)
    indices = np.array([int(r['frame_index']) for r in rows], dtype=np.int64)
    intervals = np.diff(sensor)/1e6
    available = np.array([int(row['in_range_pixels']) > 0 for row in rows], dtype=bool)
    longest_invalid = current_invalid = 0
    for has_depth in available:
        current_invalid = 0 if has_depth else current_invalid+1
        longest_invalid = max(longest_invalid,current_invalid)
    host = np.array([float(row['host_elapsed_s']) for row in rows])
    report = {
        'source': config['source'], 'received_frames': len(rows),
        'sensor_duration_seconds': float((sensor[-1]-sensor[0])/1e6),
        'received_fps_from_sensor_timestamps': float((len(rows)-1)/((sensor[-1]-sensor[0])/1e6)) if len(rows)>1 and sensor[-1]>sensor[0] else None,
        'max_sensor_interval_seconds': float(intervals.max()) if len(intervals) else None,
        'non_increasing_sensor_timestamps': int(np.count_nonzero(intervals <= 0)),
        'frame_index_gaps': int(np.maximum(np.diff(indices)-1, 0).sum()),
        'all_invalid_frames': int((~available).sum()),
        'all_invalid_frame_fraction': float((~available).mean()),
        'longest_consecutive_all_invalid_frames': int(longest_invalid),
        'host_read_duration_seconds': float(host[-1]-host[0]),
        'received_fps_from_host_read_times': float((len(rows)-1)/(host[-1]-host[0])) if len(rows)>1 and host[-1]>host[0] else None,
        'last_frame_valid_fraction': float(valid.mean()),
        'last_frame_center_raw': int(depth[depth.shape[0]//2,depth.shape[1]//2]),
        'note': 'SDK maximum is an encoding/driver limit, not a guaranteed usable physical range. No alignment or accuracy acceptance implied.',
    }
    if 'central_roi' in config and 'center_raw' in rows[0]:
        selected = [row for row in rows if int(row['device_timestamp_us'])-int(sensor[0]) >= args.warmup_seconds*1e6]
        scale = config['millimeters_per_raw_unit']
        centers = [int(row['center_raw']) for row in selected]
        center_valid = [z for z in centers if z > 0 and config['sdk_min_raw'] <= z <= config['sdk_max_raw']]
        roi = config['central_roi']
        roi_area = roi['width']*roi['height']
        roi_medians = [float(row['roi_median_raw'])*scale for row in selected if row['roi_median_raw']]
        report['central_surface_check'] = {
            'roi': roi, 'warmup_excluded_sensor_seconds': args.warmup_seconds,
            'remaining_frames': len(selected),
            'center_valid_frame_fraction': len(center_valid)/len(selected) if selected else None,
            'center_depth_mm': statistics(np.array(center_valid)*scale),
            'roi_valid_fraction_per_frame': statistics([int(row['roi_valid_pixels'])/roi_area for row in selected]),
            'roi_frame_median_depth_mm': statistics(roi_medians),
            'interpretation': 'Center is a fixed pixel ray. ROI is not object segmentation. Temporal variation assumes a stationary scene and is not absolute accuracy. ROI median assumes valid samples mostly belong to one surface.',
        }
        ys, xs = np.nonzero(valid)
        report['last_valid_pixel_bounds'] = {
            'x_min': int(xs.min()), 'x_max': int(xs.max()),
            'y_min': int(ys.min()), 'y_max': int(ys.max()),
        } if len(xs) else None
        report['valid_fraction_per_frame'] = statistics([int(row['in_range_pixels'])/depth.size for row in rows])
    # This fixed 0-5m scale is visualization only; calculations always use depth_raw.pgm.
    display = np.clip(depth*config['millimeters_per_raw_unit']/5000*255,0,255).astype(np.uint8)
    preview = cv2.applyColorMap(display,cv2.COLORMAP_TURBO)
    preview[~valid] = 0
    if not cv2.imwrite(str(folder/'depth_preview.png'), preview):
        raise RuntimeError('Cannot save preview')
    if 'central_roi' in config:
        annotated = preview.copy()
        roi = config['central_roi']
        cv2.rectangle(annotated, (roi['x'],roi['y']),
                      (roi['x']+roi['width']-1,roi['y']+roi['height']-1),(255,255,255),1)
        cv2.drawMarker(annotated,(depth.shape[1]//2,depth.shape[0]//2),(255,255,255),
                       markerType=cv2.MARKER_CROSS,markerSize=9,thickness=1)
        cv2.putText(annotated,'White box: fixed ROI, not object mask',(8,20),
                    cv2.FONT_HERSHEY_SIMPLEX,0.42,(255,255,255),1)
        if not cv2.imwrite(str(folder/'depth_roi_preview.png'),annotated):
            raise RuntimeError('Cannot save ROI preview')
    for name in ('depth_first','depth_middle'):
        raw = cv2.imread(str(folder/(name+'.pgm')),cv2.IMREAD_UNCHANGED)
        if raw is None:
            continue
        mask=(raw>0)&(raw>=config['sdk_min_raw'])&(raw<=config['sdk_max_raw'])
        colored=cv2.applyColorMap(np.clip(raw*config['millimeters_per_raw_unit']/5000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
        colored[~mask]=0
        if not cv2.imwrite(str(folder/(name+'_preview.png')),colored):
            raise RuntimeError('Cannot save snapshot preview')
    (folder/'depth_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
