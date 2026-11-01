"""Inspect SDK hardware D2C with static-scene RGB, preserving original diagnostic frames."""
import argparse
import json
from pathlib import Path
import signal
import threading
import time
import cv2
import numpy as np
from depth_camera import DepthCamera,utc_now


def diagnostic_overlay(rgb,depth):
    valid=((depth>=600)&(depth<=4000)).astype(np.uint8)
    edges=cv2.morphologyEx(valid,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    overlay=rgb.copy(); overlay[edges]=(0,255,0)
    return overlay


def pair_is_fresh(pair,now):
    return pair is not None and pair['host_time_delta']<.2 and max(
        now-pair['depth']['host_monotonic'],now-pair['color']['host_monotonic'])<=.5


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='/dev/video0')
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--raw',action='store_true',help='Baseline without hardware registration')
    parser.add_argument('--control',type=Path,default=Path('logs/rgbd-registration.control.json'))
    args=parser.parse_args()
    if not 0<args.seconds<=3600:
        parser.error('seconds must be 0..3600')
    args.output.mkdir(parents=True,exist_ok=False)
    stop=threading.Event(); lock=threading.Lock(); shared={'frame':None,'error':None,'config':None}
    last=None; displayed=None; frozen=None; anchor=None; last_command=None; sequence=0; overlay_enabled=True
    matches=[]; seen=False
    title='Astra RGB-Depth registration check - left depth / right RGB - E overlay S save Q close'

    def color_worker():
        cap=cv2.VideoCapture(args.device,cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                raise RuntimeError('Cannot open UVC color')
            cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
            cap.set(cv2.CAP_PROP_FPS,30)
            config={'width':int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    'height':int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    'requested_fps':30,'returned_nominal_fps':cap.get(cv2.CAP_PROP_FPS),
                    'transport_fourcc':int(cap.get(cv2.CAP_PROP_FOURCC)),
                    'decoded_format':'BGR uint8','software_flip':False}
            if (config['width'],config['height'])!=(640,480):
                raise RuntimeError('Color is not actual 640x480')
            with lock:
                shared['config']=config
            count=0
            while not stop.is_set():
                ok,image=cap.read()
                if not ok or image is None or image.shape!=(480,640,3):
                    raise RuntimeError('Color frame read/shape failed')
                count+=1
                frame={'image':image,'host_monotonic':time.monotonic(),
                       'host_received_utc':utc_now(),'host_read_index':count}
                with lock:
                    shared['frame']=frame
        except Exception as error:
            with lock:
                shared['error']=str(error)
        finally:
            cap.release()

    def save_pair(pair,tag,extra=None):
        nonlocal sequence
        sequence+=1; stem=f'{tag}_{sequence:04d}'
        rgb,frame=pair['color'],pair['depth']
        arrays=[('_rgb.png',rgb['image']),('_depth.pgm',frame['raw']),
                ('_overlay.png',diagnostic_overlay(rgb['image'],frame['raw']))]
        for suffix,array in arrays:
            if not cv2.imwrite(str(args.output/(stem+suffix)),array):
                raise RuntimeError('Cannot save RGB-depth evidence')
        record={'source':'REAL_CAMERA','depth_file':stem+'_depth.pgm','rgb_file':stem+'_rgb.png',
                'overlay_file':stem+'_overlay.png','registration_mode':0 if args.raw else 1,
                'depth_pixel_domain':'UNREGISTERED_DEPTH' if args.raw else 'SDK_DEPTH_TO_COLOR_OUTPUT',
                'depth_mirroring':False,'color_software_flip':False,
                'depth_frame_index':frame['frame_index'],'depth_device_timestamp_us':frame['device_timestamp_us'],
                'depth_host_received_utc':frame['host_received_utc'],
                'color_host_received_utc':rgb['host_received_utc'],'color_host_read_index':rgb['host_read_index'],
                'host_read_return_time_difference_seconds':pair['host_time_delta'],
                'saved_at_utc':utc_now(),
                'pair_age_when_saved_seconds':max(time.monotonic()-frame['host_monotonic'],time.monotonic()-rgb['host_monotonic']),
                'historical_frame_annotation':pair is frozen,
                'pairing_note':'Nearest available host-return times only; not exposure synchronization',
                'hardware_synchronized':False,'geometry_verified':False,'rgb_depth_alignment_verified':False,
                'physical_accuracy_verified':False,'position_m':None,
                'depth_note':'SDK registered output, not original raw depth' if not args.raw else 'Original unregistered metric depth'}
        if extra:
            record.update(extra)
        (args.output/(stem+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        print('PAIR_SAVED '+json.dumps(record),flush=True)
        return record

    def mouse(event,x,y,_flags,_param):
        nonlocal frozen,anchor,overlay_enabled
        if event!=cv2.EVENT_LBUTTONDOWN or not 0<=y<480 or not 0<=x<1280:
            return
        if x<640 and displayed is not None:
            if frozen is None and not pair_is_fresh(displayed,time.monotonic()):
                print('MATCH_REJECTED: displayed live pair is stale',flush=True)
                return
            value=int(displayed['depth']['raw'][y,x])
            if not 600<=value<=4000:
                print('MATCH_REJECTED: selected depth pixel has no accepted depth',flush=True)
                return
            frozen=displayed; anchor=[int(x),int(y)]; overlay_enabled=False
            print('MATCH_FROZEN: select same physical feature in right RGB image',flush=True)
        elif x>=640 and frozen is not None:
            rgb_uv=[int(x-640),int(y)]
            delta=np.asarray(rgb_uv)-anchor
            match={'source':'USER_INDICATED_IMAGE_CORRESPONDENCE','depth_uv':anchor,
                   'rgb_uv':rgb_uv,'rgb_minus_depth_uv':delta.tolist(),
                   'pixel_residual_norm':float(np.linalg.norm(delta)),
                   'registered_depth_value':int(frozen['depth']['raw'][anchor[1],anchor[0]]),
                   'note':'Manual feature localization error included; not metric calibration or XYZ accuracy'}
            save_pair(frozen,'correspondence',{'manual_correspondence':match})
            matches.append(match)
            (args.output/'correspondences.json').write_text(json.dumps(matches,indent=2)+'\n')
            print('MATCH_RESULT '+json.dumps(match),flush=True)
            frozen=None; anchor=None

    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    worker=threading.Thread(target=color_worker,daemon=True); worker.start()
    try:
        with DepthCamera(registered=not args.raw) as camera:
            cv2.namedWindow(title,cv2.WINDOW_AUTOSIZE); cv2.setMouseCallback(title,mouse)
            started=last_depth=last_render=time.monotonic(); count=0; autosaved=False; wrote_config=False
            while not stop.is_set() and time.monotonic()-started<args.seconds:
                frame=camera.read(); now=time.monotonic()
                with lock:
                    color=shared['frame']; error=shared['error']; config=shared['config']
                if error:
                    raise RuntimeError(error)
                if frame is not None:
                    last_depth=now; count+=1
                if config is not None and not wrote_config:
                    metadata={'source':'REAL_CAMERA','depth':camera.metadata,'color':config,
                              'hardware_registration_requested':not args.raw,
                              'registration_mode_read_back':camera.metadata['registration_mode'],
                              'synchronization_verified':False,'alignment_verified':False,
                              'scene_assumption':'Camera and scene kept static; host times are not exposure times'}
                    (args.output/'session.json').write_text(json.dumps(metadata,indent=2)+'\n')
                    print('RGBD_PREVIEW_READY '+json.dumps(metadata),flush=True); wrote_config=True
                fresh=(frame is not None and color is not None and now-color['host_monotonic']<.3)
                if fresh:
                    pair={'depth':frame,'color':color,
                          'host_time_delta':abs(frame['host_monotonic']-color['host_monotonic'])}
                    if pair['host_time_delta']<.2:
                        last=pair
                        if count>=30 and not autosaved:
                            save_pair(pair,'auto'); autosaved=True
                if now-last_render>.1:
                    pair=frozen if frozen is not None else last
                    if pair is not None and (frozen is not None or pair_is_fresh(pair,now)):
                        raw=pair['depth']['raw']; rgb=pair['color']['image']
                        accepted=(raw>=600)&(raw<=4000)
                        left=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                        left[~accepted]=0
                        right=diagnostic_overlay(rgb,raw) if overlay_enabled else rgb.copy()
                        cv2.putText(left,'REGISTERED DEPTH' if not args.raw else 'RAW DEPTH BASELINE',(8,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                        cv2.putText(right,'RGB + diagnostic depth edges' if overlay_enabled else 'RGB ONLY',(8,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,255,255),1)
                        if anchor is not None:
                            cv2.drawMarker(left,tuple(anchor),(255,255,255),cv2.MARKER_CROSS,19,1)
                        footer=np.zeros((100,1280,3),np.uint8)
                        lines=['FROZEN: click matching RGB feature on right; ESC cancels' if frozen is not None else 'LIVE: left-click a depth feature, then the SAME physical RGB feature on right',
                               'Hardware D2C '+('OFF' if args.raw else 'ON')+' | depth mirror OFF | E: overlay S: save Q: close',
                               f'Host-return time difference {pair["host_time_delta"]*1000:.1f} ms; exposures NOT synchronized',
                               'Diagnostic alignment only; no XYZ or grasp output. Green contours do not certify registration accuracy.']
                        for i,line in enumerate(lines):
                            cv2.putText(footer,line,(8,20+i*24),cv2.FONT_HERSHEY_SIMPLEX,.43,(0,255,255),1)
                        cv2.imshow(title,np.vstack((np.hstack((left,right)),footer)))
                        displayed=pair
                    else:
                        warning=np.zeros((580,1280,3),np.uint8)
                        cv2.putText(warning,'WAITING FOR FRESH RGB AND DEPTH',(20,45),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,0,255),1)
                        cv2.imshow(title,warning); displayed=None
                    last_render=now
                key=cv2.waitKey(1)&255
                if key in (ord('q'),ord('Q')):
                    break
                if key==27:
                    frozen=None; anchor=None
                if key in (ord('e'),ord('E')):
                    overlay_enabled=not overlay_enabled
                if key in (ord('s'),ord('S')) and (frozen is not None or pair_is_fresh(last,now)):
                    save_pair(frozen if frozen is not None else last,'user_snapshot')
                if args.control.exists():
                    try:
                        command=json.loads(args.control.read_text())
                        if command.get('id') is not None and command['id']!=last_command:
                            last_command=command['id']
                            if command.get('action')=='stop':stop.set()
                            elif command.get('action')=='save' and pair_is_fresh(last,now):save_pair(last,'chat_snapshot')
                    except (OSError,ValueError) as error:
                        print('CONTROL_ERROR '+str(error),flush=True)
                visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
                if visible>=1:seen=True
                elif seen and visible<1:break
                if now-last_depth>5 or (color is None and now-started>8) or (color is not None and now-color['host_monotonic']>5):
                    raise RuntimeError('No fresh depth/color input')
            print('RGBD_PREVIEW_CLOSED depth_frames='+str(count),flush=True)
    finally:
        stop.set(); worker.join(timeout=2)
        cv2.destroyAllWindows()


if __name__=='__main__':
    main()
