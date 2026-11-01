"""Fixed-camera depth foreground candidates and SDK-model surface positions."""
import argparse
import atexit
from dataclasses import asdict
import json
from pathlib import Path
import signal
import shutil
import time
import cv2
import numpy as np
from depth_camera import DepthCamera, utc_now
from depth_foreground import Settings, build_background, find_foreground, self_check


def iou(a,b):
    x=max(a[0],b[0]); y=max(a[1],b[1])
    width=max(0,min(a[0]+a[2],b[0]+b[2])-x)
    height=max(0,min(a[1]+a[3],b[1]+b[3])-y)
    overlap=width*height
    return overlap/max(1,a[2]*a[3]+b[2]*b[3]-overlap)


def publish_latest(output, observation):
    temp=output/'latest.tmp'
    temp.write_text(json.dumps(observation,indent=2)+'\n')
    temp.replace(output/'latest.json')


def load_background(path, metadata, settings):
    """Resume only an unchanged, user-confirmed fixed-camera scene."""
    previous=json.loads((path.parent/'session.json').read_text())
    keys=('source','width','height','pixel_format_enum','millimeters_per_raw_unit',
          'mirroring','registration_mode','factory_depth_intrinsics_fx_fy_cx_cy',
          'factory_depth_distortion_k1_k2_p1_p2_k3')
    if any(previous.get(key)!=metadata.get(key) for key in keys):
        raise ValueError('Saved background camera configuration/calibration differs')
    if previous['settings']!=asdict(settings):
        raise ValueError('Saved background settings differ')
    with np.load(path,allow_pickle=False) as archive:
        background={key:archive[key].copy() for key in archive.files}
    shape=(metadata['height'],metadata['width'])
    if any(background[key].shape!=shape for key in ('median_raw','valid','mad_raw','valid_observations')):
        raise ValueError('Saved background dimensions differ')
    if background['valid'].dtype!=np.bool_ or not np.isfinite(background['median_raw']).all():
        raise ValueError('Invalid saved background')
    return background


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--auto-background',action='store_true')
    parser.add_argument('--load-background',type=Path,
                        help='Resume a saved background only while camera/background remain fixed')
    parser.add_argument('--background-frames',type=int,default=60)
    parser.add_argument('--control',type=Path,default=Path('logs/depth-target.control.json'))
    parser.add_argument('--max-seconds',type=float,default=0)
    parser.add_argument('--foreground-delta-mm',type=float,default=50)
    args=parser.parse_args()
    if args.auto_background and args.load_background:
        parser.error('Choose either a new empty background or an existing fixed background')
    if not 15<=args.background_frames<=300 or args.foreground_delta_mm<=0 or args.max_seconds<0:
        parser.error('Invalid background count, foreground delta or duration')
    print(self_check(),flush=True)
    args.output.mkdir(parents=True,exist_ok=False)
    def invalidate_on_exit():
        publish_latest(args.output,{'source':'REAL_CAMERA','valid':False,'position_m':None,
                                   'reason':'SESSION_CLOSED','host_processed_utc':utc_now()})
    atexit.register(invalidate_on_exit)
    settings=Settings(foreground_delta_mm=args.foreground_delta_mm)
    stopping=False

    def stop(_signal,_frame):
        nonlocal stopping
        stopping=True

    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    title='Astra Pro Plus - automatic depth foreground - B: background S: save Q: close'
    background=None; collecting=args.auto_background; samples=[]; sample_stamps=[]
    bg_id=0; bg_file=None; snapshot_id=0; last=None
    previous=None; streak=0; stable_since=0.0; auto_saved=False; empty_frames=0
    seen_window=False; last_command=None

    def save_snapshot(observation,frame,mask,canvas,tag):
        nonlocal snapshot_id
        snapshot_id+=1
        stem=f'{tag}_{snapshot_id:04d}'
        for name,array in [('depth',frame['raw']),('mask',mask),('preview',canvas)]:
            suffix='.pgm' if name=='depth' else '.png'
            if not cv2.imwrite(str(args.output/(stem+'_'+name+suffix)),array):
                raise RuntimeError('Cannot save candidate evidence')
        record=dict(observation,raw_depth_file=stem+'_depth.pgm',mask_file=stem+'_mask.png',
                    preview_file=stem+'_preview.png',snapshot_trigger=tag,
                    background_file=bg_file)
        (args.output/(stem+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        print('SNAPSHOT_SAVED '+json.dumps(record),flush=True)

    with DepthCamera() as camera, (args.output/'observations.jsonl').open('w',buffering=1) as events:
        metadata=dict(camera.metadata,method='Fixed-camera depth background subtraction',
                      settings=asdict(settings),background_frames=args.background_frames,
                      temporal_gate={'minimum_processed_frames':8,'minimum_seconds':0.5,
                                     'minimum_bbox_iou':0.6,'maximum_depth_change_mm':30},
                      semantic_classification=False,
                      maximum_frame_age_seconds=0.5,
                      output_reference='One real valid pixel on visible foreground surface, not object center')
        (args.output/'session.json').write_text(json.dumps(metadata,indent=2)+'\n')
        print('TARGET_MODE_READY '+json.dumps(metadata),flush=True)
        if args.load_background:
            background=load_background(args.load_background,camera.metadata,settings)
            bg_id=1; bg_file='background_001.npz'
            shutil.copyfile(args.load_background,args.output/bg_file)
            record={'source':'REAL_CAMERA','resumed_from':str(args.load_background.resolve()),
                    'eligible_pixels':int(background['valid'].sum()),
                    'assumption':'Camera and original background kept fixed; current object not learned as background'}
            (args.output/'background_001.json').write_text(json.dumps(record,indent=2)+'\n')
            print('BACKGROUND_RESUMED '+json.dumps(record),flush=True)
        cv2.namedWindow(title,cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title,1280,560)
        started=last_received=last_processed=time.monotonic()
        received=processed=0; stale_reported=False
        while not stopping:
            frame=camera.read()
            now=time.monotonic()
            if frame is not None:
                last_received=now; received+=1; stale_reported=False
                if collecting and received>=30:
                    samples.append(frame['raw'])
                    sample_stamps.append(frame['device_timestamp_us'])
                    if len(samples)>=args.background_frames:
                        bg_id+=1
                        background=build_background(samples,settings)
                        stem=f'background_{bg_id:03d}'
                        bg_file=stem+'.npz'
                        np.savez_compressed(args.output/bg_file,**background)
                        np.save(args.output/(stem+'_raw_frames.npy'),np.stack(samples))
                        record={'source':'REAL_CAMERA','frames':len(samples),
                                'device_timestamps_us':sample_stamps,
                                'eligible_pixels':int(background['valid'].sum()),
                                'eligible_fraction':float(background['valid'].mean()),
                                'assumption':'User-reported empty scene, fixed camera and static surfaces',
                                'median_note':'Per-pixel temporal median; only stable, frequently valid pixels eligible'}
                        (args.output/(stem+'.json')).write_text(json.dumps(record,indent=2)+'\n')
                        collecting=False; samples=[]; sample_stamps=[]
                        previous=None; streak=0; auto_saved=False
                        print('BACKGROUND_READY '+json.dumps(record),flush=True)
                if now-last_processed>=0.1:
                    raw=frame['raw']
                    mask=np.zeros(raw.shape,np.uint8)
                    observation={'valid':False,'position_m':None,
                                 'reason':'COLLECTING_BACKGROUND' if collecting else 'BACKGROUND_REQUIRED'}
                    if background is not None and not collecting:
                        observation,mask=find_foreground(raw,background,settings)
                        if observation['valid']:
                            consistent=(previous is not None and
                                        iou(observation['bbox_xywh'],previous['bbox_xywh'])>=0.6 and
                                        abs(observation['depth_mm']-previous['depth_mm'])<=30)
                            streak=streak+1 if consistent else 1
                            if not consistent:
                                stable_since=now
                            previous=dict(observation)
                            observation['stable_processed_frames']=streak
                            observation['stable_host_seconds']=now-stable_since
                            if streak<8 or now-stable_since<0.5:
                                observation.update(valid=False,reason='CANDIDATE_NOT_STABLE',position_m=None)
                            empty_frames=0
                        else:
                            previous=None; streak=0
                            if observation['reason']=='NO_FOREGROUND':
                                empty_frames+=1
                                if empty_frames>=30:
                                    auto_saved=False
                    age=time.monotonic()-frame['host_monotonic']
                    if age>0.5:
                        observation.update(valid=False,reason='STALE_FRAME',position_m=None)
                    if observation['valid']:
                        u,v=observation['pixel_uv']
                        observation['position_m']=camera.optical_xyz_m(u,v,observation['raw_depth'])
                        observation['reason']='STABLE_FOREGROUND_SURFACE'
                    observation.update(source='REAL_CAMERA',host_processed_utc=utc_now(),
                                       host_received_utc=frame['host_received_utc'],
                                       device_timestamp_us=frame['device_timestamp_us'],
                                       frame_index=frame['frame_index'],frame_age_seconds=age,
                                       coordinate_frame=camera.metadata['coordinate_frame'],length_unit='m',
                                       geometry_verified=False,physical_accuracy_verified=False,
                                       geometry_status=camera.metadata['geometry_status'],
                                       semantic_class=None,
                                       reference_point='Representative visible foreground surface pixel; not object center',
                                       valid_semantics='Segmentation, temporal and depth gates only; not physical-accuracy certification')
                    observation['raw_depth_quality']={
                        'no_depth_pixels':int((raw==0).sum()),
                        'nonzero_below_working_range_pixels':int(((raw>0)&(raw<settings.minimum_mm)).sum()),
                        'above_working_range_pixels':int((raw>settings.maximum_mm).sum()),
                        'within_working_range_pixels':int(((raw>=settings.minimum_mm)&(raw<=settings.maximum_mm)).sum())}
                    if background is not None:
                        observation['raw_depth_quality']['comparable_background_pixels']=int(
                            (background['valid']&(raw>=settings.minimum_mm)&(raw<=settings.maximum_mm)).sum())
                    events.write(json.dumps(observation)+'\n')
                    publish_latest(args.output,observation)
                    valid=(raw>=settings.minimum_mm)&(raw<=settings.maximum_mm)
                    left=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                    left[~valid]=0
                    right=np.zeros_like(left)
                    if background is not None:
                        right[background['valid']]=(35,35,35)
                    right[mask>0]=(255,255,255)
                    if 'bbox_xywh' in observation:
                        x,y,w,h=observation['bbox_xywh']; u,v=observation['pixel_uv']
                        color=(0,255,0) if observation['valid'] else (0,200,255)
                        cv2.rectangle(left,(x,y),(x+w-1,y+h-1),color,1)
                        cv2.drawMarker(left,(u,v),color,cv2.MARKER_CROSS,15,1)
                        cv2.drawMarker(right,(u,v),(0,0,255),cv2.MARKER_CROSS,15,1)
                    cv2.putText(left,'DEPTH ONLY - scene distances',(8,22),cv2.FONT_HERSHEY_SIMPLEX,0.48,(0,255,255),1)
                    cv2.putText(right,'Foreground mask; gray = eligible background',(8,22),cv2.FONT_HERSHEY_SIMPLEX,0.45,(0,255,255),1)
                    footer=np.zeros((78,raw.shape[1]*2,3),np.uint8)
                    text=(f'Collecting EMPTY background {len(samples)}/{args.background_frames}; keep target out'
                          if collecting else observation['reason'])
                    coords='Surface XYZ: --'
                    if observation['valid']:
                        coords='Surface XYZ mm: '+', '.join(f'{x*1000:.1f}' for x in observation['position_m'])
                    for i,line in enumerate([text+' | B: new empty background | S: save | Q: close',
                                             coords+' | SDK geometry unverified',
                                             'One foreground region, not a class label or object center. Put object closer than background; withdraw hand.']):
                        cv2.putText(footer,line,(8,20+24*i),cv2.FONT_HERSHEY_SIMPLEX,0.44,(0,255,255),1)
                    canvas=np.vstack((np.hstack((left,right)),footer))
                    cv2.imshow(title,canvas)
                    last=(observation,frame,mask,canvas)
                    if observation['valid'] and not auto_saved:
                        save_snapshot(*last,'auto_candidate')
                        auto_saved=True
                    processed+=1; last_processed=now
            if now-last_received>0.5 and not stale_reported:
                observation={'source':'REAL_CAMERA','valid':False,'position_m':None,
                             'reason':'NO_FRESH_DEPTH_FRAME','host_processed_utc':utc_now(),
                             'seconds_since_last_frame':now-last_received}
                publish_latest(args.output,observation)
                events.write(json.dumps(observation)+'\n')
                previous=None; streak=0; last=None; stale_reported=True
                warning=np.zeros((558,1280,3),np.uint8)
                cv2.putText(warning,'NO FRESH DEPTH FRAME - position invalid',(20,50),
                            cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,0,255),2)
                cv2.imshow(title,warning)
            key=cv2.waitKey(1)&255
            if key in (ord('q'),27):
                break
            if key in (ord('b'),ord('B')):
                collecting=True; samples=[]; sample_stamps=[]; previous=None; streak=0
            if key in (ord('s'),ord('S')) and last:
                save_snapshot(*last,'user_snapshot')
            if args.control.exists():
                try:
                    command=json.loads(args.control.read_text())
                    if command.get('id') is not None and command['id']!=last_command:
                        last_command=command['id']
                        if command.get('action')=='stop':
                            stopping=True
                        elif command.get('action')=='background':
                            collecting=True; samples=[]; sample_stamps=[]; previous=None; streak=0
                        elif command.get('action')=='save' and last:
                            save_snapshot(*last,'chat_snapshot')
                except (ValueError,OSError) as error:
                    print('CONTROL_ERROR '+str(error),flush=True)
            visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
            if visible>=1:
                seen_window=True
            elif seen_window and visible==0:
                break
            if now-last_received>10:
                raise RuntimeError('No depth frame for 10 seconds')
            if args.max_seconds and now-started>=args.max_seconds:
                break
        print(f'TARGET_MODE_CLOSED depth_frames={received} processed={processed}',flush=True)
    cv2.destroyAllWindows()


if __name__=='__main__':
    main()
