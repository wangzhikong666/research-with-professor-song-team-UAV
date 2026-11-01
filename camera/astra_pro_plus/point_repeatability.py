"""Record one user-indicated static depth pixel and SDK XYZ without spatial filtering."""
import argparse
import csv
import json
from pathlib import Path
import signal
import time
import cv2
import numpy as np
from depth_camera import DepthCamera,utc_now
from point_quality import assess_pixel
from analyze_point_repeatability import analyze


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--frames',type=int,default=300)
    parser.add_argument('--max-seconds',type=float,default=45)
    args=parser.parse_args()
    if not 30<=args.frames<=3000 or args.max_seconds<=0:
        parser.error('Invalid frame count or duration')
    selection=json.loads(args.selection.read_text())
    original=json.loads((args.selection.parent/'session.json').read_text())
    if selection.get('source')!='REAL_CAMERA' or not selection.get('selected_pixel'):
        parser.error('Requires an actual saved user pixel selection')
    u,v=selection['selected_pixel']['pixel_uv']
    if not (2<=u<638 and 2<=v<478):
        parser.error('Selected pixel must support a 5x5 raw patch')
    args.output.mkdir(parents=True,exist_ok=False)
    stopping=False; rows=[]; patches=[]; last_render=0; seen=False; rejected_snapshots=0
    title='Astra fixed surface pixel - recording SDK XYZ - Q: stop'

    def stop(_signal,_frame):
        nonlocal stopping
        stopping=True

    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        with DepthCamera() as camera, (args.output/'samples.jsonl').open('w',buffering=1) as events, (args.output/'samples.csv').open('w') as csvfile:
            keys=('width','height','pixel_format_enum','millimeters_per_raw_unit','mirroring',
                  'registration_mode','factory_depth_intrinsics_fx_fy_cx_cy',
                  'factory_depth_distortion_k1_k2_p1_p2_k3')
            if any(original.get(k)!=camera.metadata.get(k) for k in keys):
                raise RuntimeError('Selection stream configuration or calibration differs; reselect point')
            metadata=dict(camera.metadata,requested_frames=args.frames,pixel_uv=[u,v],
                          selection_file=str(args.selection.resolve()),selection_record=selection,
                          reference_point='User-indicated backpack visible surface pixel; fixed image ray, not object center or tracking',
                          static_scene_assumption='User keeps camera, chair and backpack fixed since selection',
                          accepted_depth_range_mm=[600,4000],warmup_received_frames=30,
                          raw_patch_xywh=[u-2,v-2,5,5],
                          physical_reference='No ruler or other independent reference; user deferred absolute accuracy test')
            (args.output/'session.json').write_text(json.dumps(metadata,indent=2)+'\n')
            print('POINT_REPEATABILITY_READY '+json.dumps({'pixel_uv':[u,v],'frames':args.frames}),flush=True)
            cv2.namedWindow(title,cv2.WINDOW_AUTOSIZE)
            started=last_received=time.monotonic(); received=0; previous=None
            writer=csv.DictWriter(csvfile,fieldnames=['frame_index','device_timestamp_us','host_received_utc',
                'raw_depth','valid','reason','x_m','y_m','z_m','valid_neighbors','depth_spread_mm'])
            writer.writeheader()
            while not stopping and len(rows)<args.frames:
                frame=camera.read(); now=time.monotonic()
                if now-started>args.max_seconds:
                    raise RuntimeError('Recording deadline reached; preserving partial evidence')
                if frame is None:
                    if now-last_received>10:
                        raise RuntimeError('No depth frame for ten seconds')
                    if now-last_received>0.5:
                        warning=np.zeros((550,640,3),np.uint8)
                        cv2.putText(warning,'NO FRESH DEPTH FRAME - no position',(10,35),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,255),1)
                        cv2.imshow(title,warning)
                else:
                    last_received=now; received+=1; raw=frame['raw']
                    result=None
                    if received>30:
                        result=assess_pixel(raw,u,v,1,600,4000,camera.minimum,camera.maximum)
                        age=time.monotonic()-frame['host_monotonic']
                        if not rows and (not result['valid'] or abs(int(raw[v,u])-selection['selected_pixel']['raw_depth'])>50):
                            cv2.imwrite(str(args.output/'selection_recheck_depth.pgm'),raw)
                            raise RuntimeError('Selected surface no longer matches initial depth/quality; requires user recheck')
                        if age>0.5:
                            result.update(valid=False,reason='STALE_FRAME',position_m=None)
                        if previous is not None and (frame['frame_index']<=previous[0] or frame['device_timestamp_us']<=previous[1]):
                            result.update(valid=False,reason='NONMONOTONIC_FRAME',position_m=None)
                        previous=(frame['frame_index'],frame['device_timestamp_us'])
                        if result['valid']:
                            result['position_m']=camera.optical_xyz_m(u,v,int(raw[v,u]))
                            result['reason']='DEPTH_QUALITY_AND_SDK_PASSED'
                        result.update(source='REAL_CAMERA',host_received_utc=frame['host_received_utc'],
                                      device_timestamp_us=frame['device_timestamp_us'],frame_index=frame['frame_index'],
                                      frame_age_seconds=age,frame_all_zero=not bool(np.any(raw)),
                                      coordinate_frame=camera.metadata['coordinate_frame'],length_unit='m',
                                      geometry_verified=False,physical_accuracy_verified=False,
                                      geometry_status=camera.metadata['geometry_status'])
                        rows.append(result); patches.append(raw[v-2:v+3,u-2:u+3].copy())
                        if not result['valid'] and rejected_snapshots<5:
                            rejected_snapshots+=1
                            stem=f'rejected_{len(rows):04d}'
                            if not cv2.imwrite(str(args.output/(stem+'_depth.pgm')),raw):
                                raise RuntimeError('Cannot save rejected-frame evidence')
                            evidence=dict(result,raw_depth_file=stem+'_depth.pgm',
                                          whole_image_zero_fraction=float((raw==0).mean()),
                                          local_5x5_zero_fraction=float((raw[v-2:v+3,u-2:u+3]==0).mean()))
                            (args.output/(stem+'.json')).write_text(json.dumps(evidence,indent=2)+'\n')
                        events.write(json.dumps(result)+'\n')
                        xyz=result['position_m'] if result['valid'] else [None]*3
                        quality=result.get('quality',{})
                        writer.writerow(dict(frame_index=frame['frame_index'],device_timestamp_us=frame['device_timestamp_us'],
                            host_received_utc=frame['host_received_utc'],raw_depth=int(raw[v,u]),valid=result['valid'],reason=result['reason'],
                            x_m=xyz[0],y_m=xyz[1],z_m=xyz[2],valid_neighbors=quality.get('valid_neighbors'),depth_spread_mm=quality.get('depth_spread_mm')))
                        csvfile.flush()
                    snapshot=len(rows) in (1,args.frames//2,args.frames) and received>30
                    if now-last_render>=.1 or snapshot:
                        image=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                        image[raw==0]=0
                        cv2.drawMarker(image,(u,v),(255,255,255),cv2.MARKER_CROSS,19,1)
                        cv2.rectangle(image,(u-2,v-2),(u+2,v+2),(255,255,255),1)
                        footer=np.zeros((90,640,3),np.uint8)
                        lines=[f'Fixed surface pixel ({u},{v}) | samples {len(rows)}/{args.frames} | raw {int(raw[v,u])} mm',
                               'Warmup' if received<=30 else result['reason'],
                               'SDK XYZ '+str([round(x*1000,2) for x in result['position_m']])+' mm' if result and result['valid'] else 'XYZ unavailable',
                               'Repeatability only; calibration and absolute accuracy unverified']
                        for i,line in enumerate(lines):
                            cv2.putText(footer,line,(7,18+i*22),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,255,255),1)
                        canvas=np.vstack((image,footer)); cv2.imshow(title,canvas); last_render=now
                        if snapshot:
                            stem=f'sample_{len(rows):04d}'
                            for suffix,array in [('_depth.pgm',raw),('_preview.png',canvas)]:
                                if not cv2.imwrite(str(args.output/(stem+suffix)),array):
                                    raise RuntimeError('Cannot save raw evidence')
                    key=cv2.waitKey(1)&255
                    if key in (ord('q'),27):
                        stopping=True
                # Pump events also when read timed out, so Q remains responsive.
                if frame is None and cv2.waitKey(1)&255 in (ord('q'),27):
                    stopping=True
                visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
                if visible>=1:
                    seen=True
                elif seen and visible<1:
                    stopping=True
            print('POINT_REPEATABILITY_CAPTURED '+str(len(rows)),flush=True)
    finally:
        np.save(args.output/'point_raw_patches.npy',np.asarray(patches,dtype=np.uint16).reshape((-1,5,5)))
        (args.output/'capture_status.json').write_text(json.dumps({'source':'REAL_CAMERA','complete':len(rows)==args.frames,'recorded_frames':len(rows),'finished_utc':utc_now()})+'\n')
        cv2.destroyAllWindows()
    analyze(args.output)


if __name__=='__main__':
    main()
