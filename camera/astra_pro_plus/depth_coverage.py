"""Raw depth coverage diagnosis: display nonzero samples without the application gate."""
import argparse
import csv
import json
from pathlib import Path
import signal
import time

import cv2
import numpy as np

from depth_camera import DepthCamera, utc_now


def coverage(raw):
    return {'pixels':int(raw.size),'zero_pixels':int((raw==0).sum()),
            'nonzero_below_600mm_pixels':int(((raw>0)&(raw<600)).sum()),
            '600_to_4000mm_pixels':int(((raw>=600)&(raw<=4000)).sum()),
            'above_4000mm_pixels':int((raw>4000).sum())}


def point_record(raw,u,v):
    z=int(raw[v,u])
    category=('NO_DEPTH' if z==0 else 'NONZERO_BELOW_600MM' if z<600 else
              'NONZERO_600_TO_4000MM' if z<=4000 else 'NONZERO_ABOVE_4000MM')
    return {'pixel_uv':[int(u),int(v)],'raw_depth':z,
            'depth_mm':z if z else None,'category':category,
            'note':'Raw sensor report only; not independently measured distance or accuracy certification'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--frames',type=int,default=300)
    parser.add_argument('--control',type=Path,default=Path('logs/depth-coverage.control.json'))
    args=parser.parse_args()
    if not 30<=args.frames<=3000:
        parser.error('frames must be 30..3000')
    args.output.mkdir(parents=True,exist_ok=False)
    title='Astra RAW depth diagnosis - click wall pixel - S: save Q: close'
    stopping=False; displayed=None; selected=None; save_id=0; last_command=None
    records=[]; patches=[]; received=0; last_render=0; last_frame=time.monotonic()
    first_device_stamp=None; stale=False; seen_window=False

    def stop(_signal,_frame):
        nonlocal stopping
        stopping=True

    def publish(record):
        temp=args.output/'latest.tmp'
        temp.write_text(json.dumps(record,indent=2)+'\n')
        temp.replace(args.output/'latest.json')

    def save(frame,canvas,tag,pixel=None):
        nonlocal save_id
        save_id+=1; stem=f'{tag}_{save_id:04d}'
        raw=frame['raw']
        old=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
        old[(raw<600)|(raw>4000)]=0
        for suffix,array in [('_depth.pgm',raw),('_preview.png',canvas),('_previous_app_preview.png',old)]:
            if not cv2.imwrite(str(args.output/(stem+suffix)),array):
                raise RuntimeError('Cannot save raw diagnostic evidence')
        data={'source':'REAL_CAMERA','raw_depth_file':stem+'_depth.pgm',
              'preview_file':stem+'_preview.png','previous_app_preview_file':stem+'_previous_app_preview.png',
              'host_received_utc':frame['host_received_utc'],
              'device_timestamp_us':frame['device_timestamp_us'],'frame_index':frame['frame_index'],
              'whole_image':coverage(raw),'center_roi':coverage(raw[210:270,280:360]),
              'center_pixel':point_record(raw,320,240),'selected_pixel':pixel,
              'frame_age_seconds':time.monotonic()-frame['host_monotonic'],
              'geometry_verified':False,'physical_accuracy_verified':False,'position_m':None}
        (args.output/(stem+'.json')).write_text(json.dumps(data,indent=2)+'\n')
        print('DIAGNOSTIC_SAVED '+json.dumps(data),flush=True)

    def mouse(event,x,y,_flags,_param):
        nonlocal selected
        if event==cv2.EVENT_LBUTTONDOWN and displayed is not None and 0<=y<480 and 0<=x<1280:
            frame,canvas=displayed
            selected=point_record(frame['raw'],x%640,y)
            save(frame,canvas,'user_click',selected)

    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        with DepthCamera() as camera, (args.output/'frames.csv').open('w') as file:
            metadata=dict(camera.metadata,method='Raw coverage diagnosis; no foreground or XYZ output',
                          application_comparison_range_mm=[600,4000],
                          display_depth_scale_mm=[0,2000],center_roi_xywh=[280,210,80,60],
                          zero_note='No valid depth returned; cannot infer physical distance from zero',
                          nonzero_note='Raw reported depth; especially below nominal range, accuracy unverified',
                          legend={'black':'raw zero','orange':'nonzero <600 mm',
                                  'green':'600..4000 mm','purple':'>4000 mm'})
            (args.output/'session.json').write_text(json.dumps(metadata,indent=2)+'\n')
            print('RAW_DIAGNOSTIC_READY '+json.dumps(metadata),flush=True)
            writer=None
            cv2.namedWindow(title,cv2.WINDOW_AUTOSIZE); cv2.setMouseCallback(title,mouse)
            while not stopping:
                frame=camera.read(); now=time.monotonic()
                if frame is not None:
                    received+=1; last_frame=now; stale=False
                    raw=frame['raw']; counts=coverage(raw); roi=coverage(raw[210:270,280:360])
                    if first_device_stamp is None:
                        first_device_stamp=frame['device_timestamp_us']
                    record={'source':'REAL_CAMERA','acquisition_fresh':True,
                            'host_received_utc':frame['host_received_utc'],
                            'device_timestamp_us':frame['device_timestamp_us'],'frame_index':frame['frame_index'],
                            **counts,**{'roi_'+k:v for k,v in roi.items()},
                            'center_raw_depth':int(raw[240,320])}
                    if writer is None:
                        writer=csv.DictWriter(file,fieldnames=list(record)); writer.writeheader()
                    writer.writerow(record); file.flush()
                    sample_index=received-30
                    if 1<=sample_index<=args.frames:
                        records.append(record); patches.append(raw[210:270,280:360].copy())
                    if now-last_render>=0.1 or sample_index in (1,args.frames//2,args.frames):
                        left=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                        left[raw==0]=0
                        right=np.zeros_like(left)
                        right[(raw>0)&(raw<600)]=(0,165,255)
                        right[(raw>=600)&(raw<=4000)]=(0,180,0)
                        right[raw>4000]=(180,0,180)
                        for image in (left,right):
                            cv2.rectangle(image,(280,210),(359,269),(255,255,255),1)
                        cv2.putText(left,'ALL NONZERO DEPTH (no 600 mm cutoff)',(8,20),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1)
                        cv2.putText(right,'BLACK=0 ORANGE=<600 GREEN=600..4000 PURPLE=>4000',(8,20),cv2.FONT_HERSHEY_SIMPLEX,.38,(255,255,255),1)
                        footer=np.zeros((150,1280,3),np.uint8)
                        rate=(received-1)*1e6/max(1,frame['device_timestamp_us']-first_device_stamp)
                        lines=[f'REAL CAMERA - sensor depth only - received {rate:.1f} fps | S: save Q: close',
                               f'Whole image: zero {100*counts["zero_pixels"]/raw.size:.1f}% | nonzero <600 {100*counts["nonzero_below_600mm_pixels"]/raw.size:.1f}% | 600..4000 {100*counts["600_to_4000mm_pixels"]/raw.size:.1f}%',
                               f'White ROI: zero {100*roi["zero_pixels"]/roi["pixels"]:.1f}% | nonzero <600 {100*roi["nonzero_below_600mm_pixels"]/roi["pixels"]:.1f}% | center raw {int(raw[240,320])} mm (0 = NO DEPTH)',
                               f'Saved statistics samples: {min(max(0,sample_index),args.frames)}/{args.frames}; fixed white ROI is diagnostic, not target mask',
                               'Click a wall pixel: inspect its raw value. Raw zero does not tell us the actual wall distance.',
                               ('Last click: '+str(selected)[:155]) if selected else 'No XYZ output; nonzero samples do not certify physical accuracy.']
                        for i,line in enumerate(lines):
                            cv2.putText(footer,line,(8,20+i*24),cv2.FONT_HERSHEY_SIMPLEX,.43,(0,255,255),1)
                        canvas=np.vstack((np.hstack((left,right)),footer))
                        cv2.imshow(title,canvas); displayed=(frame,canvas); last_render=now
                        publish(record)
                    if sample_index in (1,args.frames//2,args.frames) and displayed is not None:
                        # Always save a canvas rendered from the exact same raw frame.
                        if displayed[0] is frame:
                            save(frame,displayed[1],'sample')
                    if sample_index==args.frames:
                        summary={'source':'REAL_CAMERA','frames':len(records),
                                 'first_device_timestamp_us':records[0]['device_timestamp_us'],
                                 'last_device_timestamp_us':records[-1]['device_timestamp_us'],
                                 'whole_image_mean_fraction':{k:float(np.mean([r[k]/r['pixels'] for r in records])) for k in counts if k!='pixels'},
                                 'center_roi_mean_fraction':{k:float(np.mean([r['roi_'+k]/r['roi_pixels'] for r in records])) for k in roi if k!='pixels'},
                                 'physical_accuracy_verified':False}
                        np.save(args.output/'center_raw_patches.npy',np.stack(patches))
                        (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
                        print('COVERAGE_SUMMARY '+json.dumps(summary),flush=True)
                elif now-last_frame>0.5 and not stale:
                    publish({'source':'REAL_CAMERA','acquisition_fresh':False,'reason':'NO_FRESH_DEPTH_FRAME',
                             'host_processed_utc':utc_now(),'position_m':None})
                    warning=np.zeros((630,1280,3),np.uint8)
                    cv2.putText(warning,'NO FRESH DEPTH FRAME',(20,50),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,255),2)
                    cv2.imshow(title,warning); displayed=None; stale=True
                key=cv2.waitKey(1)&255
                if key in (ord('q'),27):
                    break
                if key in (ord('s'),ord('S')) and displayed:
                    save(*displayed,'user_snapshot')
                if args.control.exists():
                    try:
                        command=json.loads(args.control.read_text())
                        if command.get('id') is not None and command['id']!=last_command:
                            last_command=command['id']
                            if command.get('action')=='stop':
                                stopping=True
                            elif command.get('action')=='save' and displayed:
                                save(*displayed,'chat_snapshot')
                    except (OSError,ValueError) as error:
                        print('CONTROL_ERROR '+str(error),flush=True)
                visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
                if visible>=1:
                    seen_window=True
                elif seen_window and visible<1:
                    break
                if now-last_frame>10:
                    raise RuntimeError('No depth frames for ten seconds')
            print(f'RAW_DIAGNOSTIC_CLOSED received={received}',flush=True)
    finally:
        publish({'source':'REAL_CAMERA','acquisition_fresh':False,'reason':'SESSION_CLOSED',
                 'host_processed_utc':utc_now(),'position_m':None})
        cv2.destroyAllWindows()


if __name__=='__main__':
    main()
