"""Audit reference logs and exact saved samples; compare availability on shared frames."""
import argparse
from collections import Counter
import json
from pathlib import Path
import cv2
import numpy as np
from rgb_roi_depth import in_box


def analyze(directory):
    session=json.loads((directory/'session.json').read_text())
    assert session['source']=='REAL_CAMERA'
    records=[]
    contents=(directory/'observations.jsonl').read_text();lines=contents.splitlines()
    for index,line in enumerate(lines):
        try:records.append(json.loads(line))
        except json.JSONDecodeError:
            if index==len(lines)-1 and not contents.endswith('\n'):continue
            raise
    groups={};checked_snapshots=0
    for r in records:
        assert r['source']=='REAL_CAMERA'
        if not r['valid']:
            assert r['position_m'] is None
        else:
            ref=r['surface_reference'];uv=r['depth_pixel_uv']
            assert ref['initial_reference_confirmed']
            assert np.isfinite(r['position_m']).all()
            assert abs(r['position_m'][2]-r['raw_depth']/1000)<1e-12
            assert abs(r['raw_depth']-ref['reference_initial_depth_mm'])<=ref['depth_band_half_width_mm']
            assert np.linalg.norm(np.asarray(uv)-ref['reference_depth_pixel_uv'])<=ref['maximum_neighbor_radius_depth_px']
            assert np.linalg.norm(np.asarray(r['native_sdk_rgb_uv'])-ref['anchor_rgb_uv'])<=ref['anchor_search_radius_rgb_px']
            assert in_box(r['native_sdk_rgb_uv'],r['selection']['rgb_box_xywh'])
            assert r['quality']['valid_neighbors']>=7
        selection=r.get('selection') or {};key=selection.get('anchor_selected_at_utc')
        if key is None or 'comparison_baseline' not in r or 'depth_frame_index' not in r:continue
        group=groups.setdefault(key,{'selection':selection,'frames':{}})
        group['frames'][r['depth_frame_index']]=r
    results=[]
    for group in groups.values():
        rows=list(group['frames'].values());valid=[r for r in rows if r['valid']]
        baseline=sum(bool(r['comparison_baseline']['valid']) for r in rows)
        seen_valid=gap=False;recoveries=0
        for row in rows:
            if row['valid']:
                if seen_valid and gap:recoveries+=1
                seen_valid=True;gap=False
            elif seen_valid:gap=True
        results.append({'selection':group['selection'],'paired_distinct_depth_frames':len(rows),
            'reference_valid_frames':len(valid),'baseline_valid_frames':baseline,
            'reference_availability':len(valid)/len(rows),'baseline_availability':baseline/len(rows),
            'same_original_pixel_frames':sum(r['same_original_depth_pixel'] for r in valid),
            'bounded_neighbor_frames':sum(not r['same_original_depth_pixel'] for r in valid),
            'reference_reasons':dict(Counter(r['reason'] for r in rows)),
            'baseline_reasons':dict(Counter(r['comparison_baseline']['reason'] for r in rows)),
            'observed_recoveries_after_one_or_more_invalid_frames':recoveries})
    for path in sorted(directory.glob('*.json')):
        if path.name in ('session.json','latest.json','reference_analysis.json'):continue
        saved=json.loads(path.read_text())
        if not saved.get('valid') or 'raw_depth_file' not in saved:continue
        raw=cv2.imread(str(directory/saved['raw_depth_file']),cv2.IMREAD_UNCHANGED)
        u,v=saved['depth_pixel_uv']
        assert raw.dtype==np.uint16 and raw.shape==(480,640)
        assert int(raw[v,u])==saved['raw_depth']
        mask=cv2.imread(str(directory/saved['depth_candidate_mask_file']),cv2.IMREAD_UNCHANGED)
        assert mask.dtype==np.uint8 and mask.shape==raw.shape and mask[v,u]>0
        checked_snapshots+=1
    return {'source':'REAL_CAMERA_LOG_AND_SAVED_SAMPLE_AUDIT','measurement_records':len(records),
            'saved_valid_raw_samples_checked':checked_snapshots,'invalid_records_have_null_position':True,
            'valid_records_pass_reference_constraints':True,'comparisons':results,
            'interpretation':'Availability of two selection policies on same frames; not position accuracy or identity tracking',
            'geometry_verified':False,'physical_accuracy_verified':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args();report=analyze(args.directory)
    (args.directory/'reference_analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
