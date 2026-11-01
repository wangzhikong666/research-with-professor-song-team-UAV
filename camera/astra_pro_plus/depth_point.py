"""Live depth pixel selection using the actual OpenNI stream and SDK conversion."""
import argparse
import ctypes as ct
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time
import cv2
import numpy as np
from point_quality import assess_pixel, self_check


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--min-depth-mm',type=float,default=600)
    parser.add_argument('--max-depth-mm',type=float,default=4000)
    parser.add_argument('--auto-test-center',action='store_true')
    parser.add_argument('--max-seconds',type=float,default=0)
    args = parser.parse_args()
    if not 0 < args.min_depth_mm < args.max_depth_mm or args.max_seconds < 0:
        parser.error('Invalid accepted depth range or duration')
    print(self_check(),flush=True)
    args.output.mkdir(parents=True,exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    sdk = ct.CDLL(str(root/'build/libastra_ir.so'))
    sdk.astra_point_open.argtypes = [ct.POINTER(ct.c_int),ct.c_int]
    sdk.astra_point_open.restype = ct.c_int
    sdk.astra_scene_read.argtypes = [ct.POINTER(ct.c_int),ct.c_void_p,ct.c_int,
                                    ct.POINTER(ct.c_uint64),ct.POINTER(ct.c_int)]
    sdk.astra_scene_read.restype = ct.c_int
    sdk.astra_depth_calibration.argtypes = [ct.POINTER(ct.c_float),ct.c_int]
    sdk.astra_depth_calibration.restype = ct.c_int
    sdk.astra_depth_distortion_state.argtypes = [ct.POINTER(ct.c_uint64)]
    sdk.astra_depth_distortion_state.restype = ct.c_int
    sdk.astra_depth_to_world.argtypes = [ct.c_float]*3+[ct.POINTER(ct.c_float)]
    sdk.astra_depth_to_world.restype = ct.c_int
    sdk.astra_world_to_depth.argtypes = [ct.c_float]*3+[ct.POINTER(ct.c_float)]
    sdk.astra_world_to_depth.restype = ct.c_int
    sdk.astra_ir_error.restype = ct.c_char_p
    sdk.astra_ir_close.argtypes = []
    sdk.astra_ir_close.restype = None
    stopping = False
    title = 'Astra Pro Plus - click depth surface for SDK XYZ - Q: close'
    displayed = None
    selected = None
    sequence = 0

    def stop(_signal,_frame):
        nonlocal stopping
        stopping = True

    def xyz(u,v,raw):
        output = (ct.c_float*3)()
        if sdk.astra_depth_to_world(u,v,raw,output) != 0:
            raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
        return [float(x) for x in output]

    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        config = (ct.c_int*13)()
        if sdk.astra_point_open(config,13) != 0:
            raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
        w,h,fps,fmt,mirror,sdk_min,sdk_max = list(config)[:7]
        if (w,h,fmt,mirror)!=(640,480,100,0):
            raise RuntimeError('Point mode requires actual 640x480 DEPTH_1_MM and mirror off')
        calibration = (ct.c_float*30)()
        if sdk.astra_depth_calibration(calibration,30) != 0:
            raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
        params = np.array(list(calibration),dtype=float)
        if not np.isfinite(params).all() or min(params[0],params[1]) <= 0:
            raise RuntimeError('Factory calibration is missing or non-finite')
        distortion = ct.c_uint64()
        distortion_status = sdk.astra_depth_distortion_state(ct.byref(distortion))
        # Synthetic Z is for checking SDK geometry, never reported as a measured target.
        left,right,top,bottom = xyz(0,h/2,1000),xyz(w-1,h/2,1000),xyz(w/2,0,1000),xyz(w/2,h-1,1000)
        fx=(w-1)*1000/(right[0]-left[0]); fy=(h-1)*1000/(top[1]-bottom[1])
        cx=-left[0]*fx/1000; cy=top[1]*fy/1000
        center,right_one,down_one = xyz(w/2,h/2,1000),xyz(w/2+1,h/2,1000),xyz(w/2,h/2+1,1000)
        if not (right_one[0]>center[0] and down_one[1]<center[1] and abs(center[2]-1000)<0.01):
            raise RuntimeError('SDK axis or depth-unit check failed')
        metadata = {
            'source':'REAL_CAMERA','started_utc':utc_now(),'width':w,'height':h,
            'nominal_fps':fps,'pixel_format_enum':fmt,'millimeters_per_raw_unit':1,
            'mirroring':False,'registration_mode':int(config[12]),
            'factory_depth_intrinsics_fx_fy_cx_cy':params[:4].tolist(),
            'factory_depth_distortion_k1_k2_p1_p2_k3':params[20:25].tolist(),
            'factory_parameters_scope':'Vendor l_intr_p/l_k record; applicability to rectified depth pixels remains unverified',
            'sdk_effective_projection_fx_fy_cx_cy':[fx,fy,cx,cy],
            'sdk_intrinsics_source':'Numerically checked using the active SDK conversion, not assumed image center',
            'factory_minus_sdk_principal_point_px':(params[2:4]-[cx,cy]).tolist(),
            'distortion_state':{'query_status':distortion_status,
                                'value':int(distortion.value) if distortion_status==0 else None},
            'coordinate_method':'Official SDK convertDepthToWorld; optical Y = minus SDK Y',
            'output_frame':'camera_depth_optical_sdk',
            'axes':'X image right; Y image down; Z forward along optical axis',
            'origin':'SDK camera optical model origin, not robot base',
            'distortion_handling':'No additional correction: output rectification state not verified; factory coefficients preserved',
            'geometry_verified':False,'physical_accuracy_verified':False,
            'geometry_note':'SDK projection differs from factory principal point; output is SDK-model geometry for learning',
            'accepted_depth_range_mm':[args.min_depth_mm,args.max_depth_mm],
            'accepted_range_note':'Application gate; does not certify accuracy over this range',
            'reference_point':'Visible surface at clicked depth pixel; not object center or grasp point',
        }
        (args.output/'calibration.json').write_text(json.dumps(metadata,indent=2)+'\n')
        print('POINT_MODE_READY '+json.dumps(metadata),flush=True)

        def measure(u,v,frame,input_type):
            nonlocal selected,sequence
            sequence += 1
            raw = frame['raw']
            result = assess_pixel(raw,u,v,1,args.min_depth_mm,args.max_depth_mm,sdk_min,sdk_max)
            result.update(source='REAL_CAMERA',input_type=input_type,measured_at_utc=utc_now(),
                          host_received_utc=frame['received_utc'],device_timestamp_us=frame['timestamp'],
                          frame_index=frame['index'],frame_age_seconds=time.monotonic()-frame['received_monotonic'],
                          coordinate_frame=metadata['output_frame'],length_unit='m',
                          geometry_verified=False,physical_accuracy_verified=False,
                          valid_semantics='Depth quality and SDK conversion only; not geometric accuracy certification',
                          reference_point=metadata['reference_point'],
                          geometry_status='SDK_MODEL_FACTORY_PRINCIPAL_DIFFERS_DISTORTION_STATE_UNVERIFIED')
            if result['frame_age_seconds']>0.5:
                result.update(valid=False,reason='STALE_FRAME',position_m=None)
            if result['valid']:
                sdk_xyz = xyz(u,v,result['raw_depth'])
                optical = np.array([sdk_xyz[0],-sdk_xyz[1],sdk_xyz[2]])
                back = (ct.c_float*3)()
                status = sdk.astra_world_to_depth(*sdk_xyz,back)
                error = float(np.linalg.norm(np.array([back[0],back[1]])-[u,v])) if status==0 else None
                if status != 0 or error>0.01 or abs(optical[2]-result['depth_mm'])>0.01:
                    result.update(valid=False,reason='SDK_ROUNDTRIP_FAILED',position_m=None)
                else:
                    result.update(position_m=(optical/1000).tolist(),display_xyz_mm=optical.tolist(),
                                  sdk_native_xyz_mm=sdk_xyz,roundtrip_pixel_error=error)
            stem=f'{input_type}_{sequence:04d}'
            raw_name=stem+'_depth.pgm'
            if not cv2.imwrite(str(args.output/raw_name),raw):
                raise RuntimeError('Cannot save selected frame raw depth')
            result['raw_depth_file']=raw_name
            (args.output/(stem+'.json')).write_text(json.dumps(result,indent=2)+'\n')
            with (args.output/'points.jsonl').open('a') as file:
                file.write(json.dumps(result)+'\n')
            selected=result
            print('POINT_RESULT '+json.dumps(result),flush=True)
            return result

        def mouse(event,x,y,_flags,_param):
            if event==cv2.EVENT_LBUTTONDOWN and displayed is not None and 0<=x<w and 0<=y<h:
                try:
                    measure(x,y,displayed,'user_click')
                except Exception as error:
                    print('CLICK_ERROR '+str(error),flush=True)

        cv2.namedWindow(title,cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(title,mouse)
        raw = np.empty((h,w),np.uint16)
        kind,index,stamp = ct.c_int(),ct.c_int(),ct.c_uint64()
        started=last_frame=last_render=time.monotonic()
        received=0; auto_done=False; seen_window=False
        while not stopping:
            status=sdk.astra_scene_read(ct.byref(kind),raw.ctypes.data,raw.nbytes,ct.byref(stamp),ct.byref(index))
            now=time.monotonic()
            if status<0:
                raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
            if status==0:
                if kind.value!=0:
                    raise RuntimeError('Unexpected IR frame in depth-only point mode')
                last_frame=now; received+=1
                if now-last_render>=1/15:
                    displayed={'raw':raw.copy(),'timestamp':stamp.value,'index':index.value,
                               'received_monotonic':now,'received_utc':utc_now()}
                    valid=(raw>0)&(raw>=sdk_min)&(raw<=sdk_max)&(raw>=args.min_depth_mm)&(raw<=args.max_depth_mm)
                    view=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                    view[~valid]=0
                    cv2.putText(view,'DEPTH ONLY - click a solid colored surface',(8,22),cv2.FONT_HERSHEY_SIMPLEX,0.47,(255,255,255),1)
                    if selected:
                        u,v=selected['pixel_uv']; color=(0,255,0) if selected['valid'] else (0,0,255)
                        cv2.drawMarker(view,(u,v),color,cv2.MARKER_CROSS,15,1)
                    footer=np.zeros((102,w,3),np.uint8)
                    lines=['Click result: none' if not selected else f'Last click (not tracking): u,v={selected["pixel_uv"]} | {selected["reason"]}',
                           'XYZ: --' if not selected or not selected['valid'] else 'XYZ (mm): '+', '.join(f'{x:.1f}' for x in selected['display_xyz_mm']),
                           'Axes: right, down, forward | exact clicked depth | Q: close',
                           'SDK model; factory principal differs; distortion state unverified']
                    for i,line in enumerate(lines):
                        cv2.putText(footer,line,(8,20+24*i),cv2.FONT_HERSHEY_SIMPLEX,0.40,(255,255,255) if i<3 else (0,210,255),1)
                    cv2.imshow(title,np.vstack((view,footer)))
                    last_render=now
                    if args.auto_test_center and not auto_done and received>=15:
                        quality=assess_pixel(displayed['raw'],w//2,h//2,1,args.min_depth_mm,args.max_depth_mm,sdk_min,sdk_max)
                        if quality['valid']:
                            measure(w//2,h//2,displayed,'live_auto_test')
                            auto_done=True
            if cv2.waitKey(1)&255 in (ord('q'),27):
                break
            visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
            if visible>=1:
                seen_window=True
            elif seen_window and visible==0:
                break
            if now-last_frame>10:
                raise RuntimeError('No new depth frame for 10 seconds')
            if args.max_seconds and now-started>=args.max_seconds:
                break
        print(f'POINT_MODE_CLOSED received={received} measurements={sequence}',flush=True)
    finally:
        sdk.astra_ir_close()
        cv2.destroyAllWindows()


if __name__=='__main__':
    main()
