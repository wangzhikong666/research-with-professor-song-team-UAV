"""Show live depth, optionally with IR, while recording unmodified depth."""
import argparse
import csv
import ctypes as ct
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time
import cv2
import numpy as np


def save_raw(path, raw):
    if not cv2.imwrite(str(path), raw):
        raise RuntimeError(f'Cannot save raw uint16 image: {path}')


class Capture:
    def __init__(self, folder, frames, config, label):
        self.folder, self.target, self.config = folder, frames, config
        folder.mkdir(parents=True, exist_ok=False)
        metadata = dict(config, started_utc=datetime.now(timezone.utc).isoformat(),
                        requested_frames=frames, scene_label=label,
                        static_scene_verified=False, live_preview_continues=True)
        (folder/'depth_config.json').write_text(json.dumps(metadata, indent=2)+'\n')
        self.file = (folder/'depth_frames.csv').open('w', newline='')
        self.writer = csv.writer(self.file)
        self.writer.writerow(['frame_index','device_timestamp_us','host_elapsed_s',
                              'nonzero_pixels','in_range_pixels','min_nonzero_raw','max_raw',
                              'center_raw','roi_valid_pixels','roi_median_raw','latest_ir_timestamp_us'])
        self.count, self.patches, self.last = 0, [], None
        self.started = time.monotonic()
        print(f'CAPTURE_STARTED {folder} frames={frames} label={label}', flush=True)

    def add(self, raw, stamp, index, ir_stamp):
        c = self.config
        valid = (raw > 0) & (raw >= c['sdk_min_raw']) & (raw <= c['sdk_max_raw'])
        roi = c['central_roi']
        patch = raw[roi['y']:roi['y']+roi['height'], roi['x']:roi['x']+roi['width']]
        pv = patch[(patch > 0) & (patch >= c['sdk_min_raw']) & (patch <= c['sdk_max_raw'])]
        nz = raw[raw > 0]
        self.writer.writerow([index,stamp,time.monotonic()-self.started,
                              int(nz.size),int(valid.sum()),int(nz.min()) if nz.size else 0,
                              int(raw.max()),int(raw[raw.shape[0]//2,raw.shape[1]//2]),
                              int(pv.size),float(np.median(pv)) if pv.size else '',ir_stamp])
        self.patches.append(patch.copy())
        self.last = raw.copy()
        self.count += 1
        if self.count == 1:
            save_raw(self.folder/'depth_first.pgm', raw)
        if self.count == self.target//2:
            save_raw(self.folder/'depth_middle.pgm', raw)
        if self.count % 30 == 0:
            self.file.flush()
        return self.count >= self.target

    def finish(self, ir_raw, complete):
        self.file.close()
        if self.last is not None:
            save_raw(self.folder/'depth_raw.pgm', self.last)
            np.save(self.folder/'central_depth_patches.npy', np.stack(self.patches))
        if ir_raw is not None:
            save_raw(self.folder/'latest_ir_raw.pgm', ir_raw)
        state = {'source':'REAL_CAMERA','complete':complete,'received_frames':self.count,
                 'requested_frames':self.target,'preview_was_active':True,
                 'ir_stream_enabled':self.config.get('ir_stream',{}).get('enabled',True),
                 'ir_exposure_synchronized_with_depth':False,
                 'ir_note':('Latest independently arriving IR frame, not a synchronized pair'
                            if ir_raw is not None else 'No IR image saved; consult stream enabled flag'),
                 'patch_units':'Unmodified depth raw units; use depth_config.json scale'}
        (self.folder/'capture_state.json').write_text(json.dumps(state,indent=2)+'\n')
        print(f'CAPTURE_FINISHED {self.folder} frames={self.count} complete={complete}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, default=Path('logs/scene-preview.control.json'))
    parser.add_argument('--depth-only', action='store_true',
                        help='Create only the depth stream; keep live display and recording')
    parser.add_argument('--auto-capture-output', type=Path)
    parser.add_argument('--auto-capture-frames', type=int, default=30)
    parser.add_argument('--max-seconds', type=float, default=0)
    parser.add_argument('--ir-display-max', type=float, default=1023,
                        help='Fixed display-only upper intensity count, not a sensor calibration')
    args = parser.parse_args()
    if not 1 <= args.auto_capture_frames <= 18000 or args.max_seconds < 0 or args.ir_display_max <= 0:
        parser.error('Invalid frame count or duration')
    root = Path(__file__).resolve().parents[2]
    sdk = ct.CDLL(str(root/'build/libastra_ir.so'))
    sdk.astra_scene_open.argtypes = [ct.POINTER(ct.c_int),ct.c_int]
    sdk.astra_scene_open.restype = ct.c_int
    sdk.astra_depth_open.argtypes = [ct.POINTER(ct.c_int),ct.c_int]
    sdk.astra_depth_open.restype = ct.c_int
    sdk.astra_scene_read.argtypes = [ct.POINTER(ct.c_int),ct.c_void_p,ct.c_int,
                                    ct.POINTER(ct.c_uint64),ct.POINTER(ct.c_int)]
    sdk.astra_scene_read.restype = ct.c_int
    sdk.astra_ir_error.restype = ct.c_char_p
    sdk.astra_ir_close.argtypes = []
    sdk.astra_ir_close.restype = None
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    capture = None
    latest_depth = latest_ir = None
    count_depth = count_ir = 0
    ir_stamp = 0
    title = ('Astra Pro Plus - DEPTH ONLY' if args.depth_only else 'Astra Pro Plus - DEPTH + IR')+' - C: record 300 frames - Q: close'
    completed = ''
    seen_window = False
    last_command = None
    try:
        values = (ct.c_int*13)()
        open_streams = sdk.astra_depth_open if args.depth_only else sdk.astra_scene_open
        if open_streams(values,13) != 0:
            raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
        dw,dh,dfps,dformat,dmirror,dmin,dmax,iw,ih,ifps,iformat,imirror,registration = list(values)
        scale = {100:1.0,101:0.1}[dformat]
        roi = {'x':(dw-dw//10)//2,'y':(dh-dh//10)//2,'width':dw//10,'height':dh//10,
               'verified_inside_target':False}
        config = {'source':'REAL_CAMERA','width':dw,'height':dh,'nominal_fps':dfps,
                  'pixel_format_enum':dformat,'millimeters_per_raw_unit':scale,
                  'sdk_min_raw':dmin,'sdk_max_raw':dmax,'mirroring':bool(dmirror),
                  'image_registration_mode':registration,'central_roi':roi,
                  'raw_file':'depth_raw.pgm','raw_encoding':'PGM uint16, unmodified depth',
                  'rgb_depth_alignment_verified':False,
                  'ir_stream':{'enabled':not args.depth_only,'width':iw,'height':ih,'nominal_fps':ifps,
                               'pixel_format_enum':iformat,'mirroring':bool(imirror)},
                  'roi_caution':'Fixed image region, not object mask; median assumes same surface',
                  'ir_display_range_counts':[0,args.ir_display_max],
                  'ir_display_note':'Fixed visual scale only; not a physical intensity calibration',
                  'depth_ir_exposure_sync_verified':False}
        print('SCENE_OPEN '+json.dumps(config),flush=True)
        buffer = np.empty(max(dw*dh,iw*ih),np.uint16)
        kind,index,stamp = ct.c_int(),ct.c_int(),ct.c_uint64()
        cv2.namedWindow(title,cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title,960 if args.depth_only else 1280,780 if args.depth_only else 520)
        started = last_frame = last_render = time.monotonic()
        depth_time = ir_time = 0.0
        if args.auto_capture_output:
            capture = Capture(args.auto_capture_output,args.auto_capture_frames,config,
                              'DIAGNOSTIC_live_preview_check_position_not_verified')
        while not stopping:
            result = sdk.astra_scene_read(ct.byref(kind),buffer.ctypes.data,buffer.nbytes,
                                         ct.byref(stamp),ct.byref(index))
            now = time.monotonic()
            if result < 0:
                raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
            if result == 0:
                last_frame = now
                if kind.value == 0:
                    latest_depth = buffer[:dw*dh].reshape(dh,dw).copy()
                    count_depth += 1
                    depth_time = now
                    if count_depth == 1:
                        print('FIRST_DEPTH_FRAME received',flush=True)
                    if capture and capture.add(latest_depth,stamp.value,index.value,ir_stamp):
                        capture.finish(latest_ir,True)
                        completed = f'SAVED {capture.count} frames'
                        capture = None
                else:
                    latest_ir = buffer[:iw*ih].reshape(ih,iw).copy()
                    count_ir += 1
                    ir_stamp = stamp.value
                    ir_time = now
                    if count_ir == 1:
                        print('FIRST_IR_FRAME received',flush=True)
            if now-last_render >= 1/15:
                left = np.zeros((dh,dw,3),np.uint8)
                roi_status = 'Waiting for depth'
                if latest_depth is not None:
                    valid = (latest_depth>0)&(latest_depth>=dmin)&(latest_depth<=dmax)
                    gray = np.clip(latest_depth*scale/2000*255,0,255).astype(np.uint8)
                    left = cv2.applyColorMap(gray,cv2.COLORMAP_TURBO)
                    left[~valid] = 0
                    patch = latest_depth[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']]
                    pv = patch[(patch>0)&(patch>=dmin)&(patch<=dmax)]
                    depth_text = f'{np.median(pv)*scale:.1f} mm' if pv.size else 'INVALID'
                    roi_status = f'ROI valid {100*pv.size/patch.size:.1f}% | median {depth_text}'
                    if not valid.any():
                        cv2.putText(left,'NO VALID DEPTH',(8,50),cv2.FONT_HERSHEY_SIMPLEX,0.65,(0,0,255),2)
                right = np.zeros((ih,iw,3),np.uint8)
                if latest_ir is not None:
                    lo,hi = np.percentile(latest_ir,[1,99])
                    gray = np.clip(latest_ir.astype(float)/args.ir_display_max*255,0,255).astype(np.uint8)
                    right = cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
                    cv2.putText(right,f'Raw IR P1..P99: {lo:.0f}..{hi:.0f}',(8,46),
                                cv2.FONT_HERSHEY_SIMPLEX,0.46,(0,255,255),1)
                panels = [(left,dw,dh,'DEPTH: distance; display scale 0-2 m')]
                if not args.depth_only:
                    panels.append((right,iw,ih,f'IR: fixed display 0..{args.ir_display_max:g} counts; not RGB'))
                for panel,w,h,label in panels:
                    x,y,rw,rh = (w-w//10)//2,(h-h//10)//2,w//10,h//10
                    cv2.rectangle(panel,(x,y),(x+rw-1,y+rh-1),(0,255,255),2)
                    cv2.putText(panel,label,(8,22),cv2.FONT_HERSHEY_SIMPLEX,0.47,(0,255,255),1)
                cv2.putText(left,roi_status,(8,dh-12),cv2.FONT_HERSHEY_SIMPLEX,0.47,(255,255,255),1)
                cv2.putText(right,'Place flat cardboard face inside yellow box',(8,ih-12),cv2.FONT_HERSHEY_SIMPLEX,0.43,(0,255,255),1)
                if latest_depth is not None and now-depth_time>2:
                    cv2.putText(left,'STALE DEPTH',(8,48),cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,0,255),2)
                if latest_ir is not None and now-ir_time>2:
                    cv2.putText(right,'STALE IR',(8,48),cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,0,255),2)
                if (iw,ih)!=(dw,dh):
                    right = cv2.resize(right,(dw,dh))
                display = left if args.depth_only else np.hstack((left,right))
                footer = np.zeros((48,display.shape[1],3),np.uint8)
                status = f'RECORDING {capture.count}/{capture.target} - preview stays live' if capture else completed or 'LIVE - align target, then C: record 300 frames'
                cv2.putText(footer,status+' | Q/Esc: close',(8,20),cv2.FONT_HERSHEY_SIMPLEX,0.44,(0,255,255),1)
                note = 'Only DEPTH active. Fixed ROI is not an object mask.' if args.depth_only else 'Fixed ROI is not an object mask. Separate stream updates; no RGB alignment or exposure-sync claim.'
                cv2.putText(footer,note,(8,40),cv2.FONT_HERSHEY_SIMPLEX,0.42,(255,255,255),1)
                cv2.imshow(title,np.vstack((display,footer)))
                last_render = now
            key = cv2.waitKey(1)&255
            if key in (ord('q'),27):
                break
            if key in (ord('c'),ord('C')) and capture is None:
                folder = root/'data'/('live-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
                capture = Capture(folder,300,config,'USER_TRIGGERED_position_not_automatically_verified')
                completed = ''
            # Some GTK/Wayland builds return -1/0 before exposing visibility. Do not close on that.
            visible = cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
            if visible >= 1:
                seen_window = True
            elif seen_window and visible == 0:
                break
            if capture is None and args.control.exists():
                try:
                    command = json.loads(args.control.read_text())
                    if command.get('id') != last_command:
                        last_command = command.get('id')
                        if command.get('action') == 'stop':
                            stopping = True
                        elif command.get('action') == 'capture':
                            frames = int(command.get('frames',300))
                            if not 1 <= frames <= 18000:
                                raise ValueError('Capture count outside 1..18000')
                            folder = Path(command['output'])
                            if not folder.is_absolute():
                                folder = root/folder
                            capture = Capture(folder,frames,config,command.get('label','CHAT_TRIGGERED'))
                            completed = ''
                except (ValueError,KeyError,OSError) as error:
                    print(f'CONTROL_ERROR {error}',flush=True)
            if now-last_frame>10:
                raise RuntimeError('No depth or IR frames for 10 seconds')
            if args.max_seconds and now-started>=args.max_seconds:
                break
        print(f'SCENE_CLOSED depth_frames={count_depth} ir_frames={count_ir}',flush=True)
    finally:
        if capture:
            capture.finish(latest_ir,False)
        sdk.astra_ir_close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
