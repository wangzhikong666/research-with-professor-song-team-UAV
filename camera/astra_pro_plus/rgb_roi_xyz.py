"""Drag a true RGB region, find a measured depth surface, and report SDK XYZ."""
import argparse
import ctypes as ct
import json
from pathlib import Path
import signal
import time
import cv2
import numpy as np
from depth_camera import DepthCamera, utc_now
from rgbd_registration import pair_is_fresh
from rgb_roi_depth import FactoryProjection, in_box, surface_candidates
from surface_reference import SurfaceReference
from uvc_stream import UvcColorStream


STATUS_TEXT={
    'RGB_BOX_REQUIRED':'Drag a region on the left RGB image',
    'RGB_DRAG_IN_PROGRESS':'Selecting region: release mouse to measure',
    'SDK_SURFACE_POINT_AVAILABLE':'Measured surface point available',
    'WAITING_FOR_CONSECUTIVE_VALID_DEPTH':'Waiting for consecutive reliable depth samples',
    'AMBIGUOUS_COMPETING_DEPTH_LAYERS':'Several separate surfaces: select just the intended surface',
    'NO_DEPTH_CONTINUOUS_PATCH':'Not enough connected measured depth in this region',
    'NO_SAFE_SURFACE_POINT':'No point with sufficient local depth support',
    'INSUFFICIENT_MAPPED_DEPTH':'Too little usable depth in the RGB region',
    'NO_USABLE_DEPTH':'No usable depth in the measurement range',
    'RGB_BOX_TOO_SMALL_OR_OUTSIDE_IMAGE':'Select a region at least 24 x 24 pixels',
    'STALE_PAIR':'Waiting for fresh RGB and depth',
    'REFERENCE_CLICK_REQUIRED':'RIGHT-click the intended surface inside your RGB box',
    'REFERENCE_CLICK_TOO_CLOSE_TO_BOX_EDGE':'Right-click farther inside your RGB box',
    'REFERENCE_CLICK_ON_DEPTH_BOUNDARY':'Click is between depth layers: right-click within one surface',
    'REFERENCE_LOCAL_DEPTH_MISSING':'Reference surface: current local depth is missing or unreliable',
    'REFERENCE_DEPTH_CHANGED_RESELECT':'Reference depth changed: restore scene or RIGHT-click a new surface',
}


def write_json(path, record):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


class SdkMapping:
    def __init__(self,camera):
        self.camera=camera
        sdk=camera.sdk
        sdk.astra_depth_registration_query.argtypes=[ct.c_int,ct.c_int,ct.c_int,ct.c_uint16,
                                                     ct.c_int,ct.c_int,ct.POINTER(ct.c_int)]
        sdk.astra_depth_registration_query.restype=ct.c_int
        parameters=(ct.c_float*30)()
        camera.check(sdk.astra_depth_calibration(parameters,30))
        self.parameters=list(parameters)
        self.projection=FactoryProjection(self.parameters)
        self.preflight=[]
        for z in (600,1000,2000):
            for u,v in ((100,100),(320,240),(500,350)):
                actual=self.pixel(u,v,z)
                projected,_=self.projection.pixels(np.array([u]),np.array([v]),np.array([z]))
                predicted=np.floor(projected[0]).astype(int).tolist()
                error=max(abs(a-b) for a,b in zip(actual,predicted))
                self.preflight.append({'source':'SYNTHETIC_API_QUERY_NOT_PHYSICAL_MEASUREMENT',
                    'input_depth_uv_raw':[u,v,z],'native_sdk_rgb_uv':actual,
                    'preselection_rgb_uv':predicted,'maximum_difference_px':error})
                if error>2:
                    raise RuntimeError('Factory preselection disagrees with native SDK D2C')

    def pixel(self,u,v,z):
        output=(ct.c_int*3)()
        status=self.camera.sdk.astra_depth_registration_query(1,int(u),int(v),int(z),640,480,output)
        if status!=0 or output[2]!=26:
            raise RuntimeError(f'SDK PIXEL_D2C query failed: status={status}, bytes={output[2]}')
        return list(output)[:2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='/dev/video0')
    parser.add_argument('--seconds',type=float,default=600)
    parser.add_argument('--compare-baseline',action='store_true',help='Evaluate previous largest-surface selector on the SAME live frames')
    parser.add_argument('--restore-selection',type=Path,help='Restore a previous static-scene box/anchor; acquire fresh depth, not old XYZ')
    parser.add_argument('--control',type=Path,default=Path('logs/rgb-roi-xyz.control.json'))
    args=parser.parse_args()
    if not 0<args.seconds<=3600:parser.error('seconds must be 0..3600')
    args.output.mkdir(parents=True,exist_ok=False)
    stopped=False; box=None; selection=None; drag=None; displayed=None; frozen=None
    last_pair=None; measured_pair=None; last_result=None; last_mask=np.zeros((480,640),np.uint8)
    last_command=None; stable=[]; sequence=0; autosaved=False; config_written=False
    reference=SurfaceReference(); statistics={'samples':0,'valid_samples':0,'baseline_valid_samples':0}
    title='Astra surface XYZ - drag LEFT RGB box, RIGHT-click surface - Q close'

    def stop(*_):
        nonlocal stopped
        stopped=True

    def select(new_box,source):
        nonlocal box,selection,stable,autosaved,last_result,statistics
        box=new_box
        selection={'source':source,'rgb_box_xywh':box,'selected_at_utc':utc_now()}
        stable=[]; autosaved=False; last_result=None
        reference.reset(); statistics={'samples':0,'valid_samples':0,'baseline_valid_samples':0}
        print('RGB_SELECTION '+json.dumps(selection),flush=True)

    def set_anchor(uv,source):
        nonlocal selection,stable,autosaved,last_result,statistics
        if box is None:return
        reference.reset(uv); stable=[]; autosaved=False; last_result=None
        selection=dict(selection,anchor_rgb_uv=list(uv),anchor_source=source,anchor_selected_at_utc=utc_now())
        statistics={'samples':0,'valid_samples':0,'baseline_valid_samples':0}
        print('SURFACE_REFERENCE_SELECTED '+json.dumps(selection),flush=True)

    def mouse(event,x,y,_flags,_param):
        nonlocal drag,frozen
        if event==cv2.EVENT_RBUTTONDOWN and 0<=x<640 and 0<=y<480:
            if displayed is not None and pair_is_fresh(displayed,time.monotonic()):
                drag=None; frozen=None; set_anchor([x,y],'USER_RGB_RIGHT_CLICK')
            return
        if event==cv2.EVENT_LBUTTONDOWN and 0<=x<640 and 0<=y<480:
            if displayed is not None and pair_is_fresh(displayed,time.monotonic()):
                drag=[x,y,x,y]; frozen=displayed
                select(None,'USER_RGB_DRAG_IN_PROGRESS')
        elif event==cv2.EVENT_MOUSEMOVE and drag is not None:
            drag[2:]=[int(np.clip(x,0,639)),int(np.clip(y,0,479))]
        elif event==cv2.EVENT_LBUTTONUP and drag is not None:
            x0,y0,x1,y1=drag
            x1,y1=int(np.clip(x,0,639)),int(np.clip(y,0,479))
            select([min(x0,x1),min(y0,y1),abs(x1-x0)+1,abs(y1-y0)+1],'USER_RGB_DRAG')
            drag=None; frozen=None

    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    if args.restore_selection:
        previous=json.loads(args.restore_selection.read_text())
        select(previous['rgb_box_xywh'],'RESTORED_PREVIOUS_SELECTION')
        if previous.get('anchor_rgb_uv') is not None:
            set_anchor(previous['anchor_rgb_uv'],'RESTORED_PREVIOUS_REFERENCE')
        selection['restored_selection']=previous
    color=UvcColorStream(args.device).start()
    try:
        with DepthCamera() as camera:
            mapping=SdkMapping(camera)
            cv2.namedWindow(title,cv2.WINDOW_AUTOSIZE); cv2.setMouseCallback(title,mouse)
            started=last_depth=last_process=time.monotonic(); seen=False

            def record_base(reason):
                return {'source':'REAL_CAMERA','valid':False,'position_m':None,'reason':reason,
                    'recorded_at_utc':utc_now(),'selection':selection,'coordinate_frame':'camera_depth_optical_sdk',
                    'axes':'right, down, forward','length_unit':'meter',
                    'reference_point':'One measured visible surface point; not object center or grasp point',
                    'geometry_verified':False,'physical_accuracy_verified':False,
                    'geometry_status':camera.metadata['geometry_status'],
                    'hardware_synchronized':False,'rgb_depth_alignment_verified':False,
                    'pairing_note':'Host read-return times only; static camera and target required',
                    'region_note':'User image region, not semantic segmentation or object tracking'}

            def checked_sample(point):
                u,v=point['depth_pixel_uv']; raw=point['raw_depth']
                sdk_uv=mapping.pixel(u,v,raw)
                delta=max(abs(a-b) for a,b in zip(sdk_uv,point['predicted_rgb_uv']))
                if delta>2 or not in_box(sdk_uv,box) or not camera.minimum<=raw<=camera.maximum:return None
                xyz=camera.optical_xyz_m(u,v,raw)
                if not np.isfinite(xyz).all():return None
                return {'depth_pixel_uv':[u,v],'native_sdk_rgb_uv':sdk_uv,
                        'raw_depth':raw,'depth_mm':raw,'model_sdk_difference_px':delta,
                        'position_m':xyz,'quality':dict(point['quality'],
                        depth_selection='Exact current original-depth sample; no substitution, filling or held XYZ')}

            def baseline_on_frame(depth,now):
                nonlocal stable
                candidates,_=surface_candidates(depth['raw'],box,mapping.projection)
                baseline={'valid':False,'reason':candidates['reason']}
                if candidates['valid']:
                    for point in candidates['candidate_points'][:3]:
                        sample=checked_sample(point)
                        if sample is None:continue
                        raw=sample['raw_depth']
                        if stable and abs(stable[-1][1]-raw)>30:stable=[]
                        if not stable or stable[-1][2]!=depth['frame_index']:
                            stable.append((now,raw,depth['frame_index']))
                        stable=stable[-4:]
                        ready=len(stable)>=3 and now-stable[0][0]>=.25
                        baseline.update(valid=ready,reason='SDK_SURFACE_POINT_AVAILABLE' if ready else 'WAITING_FOR_CONSECUTIVE_VALID_DEPTH')
                        break
                if not baseline['valid'] and baseline['reason']!='WAITING_FOR_CONSECUTIVE_VALID_DEPTH':stable=[]
                return baseline

            def save(pair,result,mask,tag):
                nonlocal sequence
                sequence+=1; stem=f'{tag}_{sequence:04d}'
                images={'rgb_file':(stem+'_rgb.png',pair['color']['image']),
                        'raw_depth_file':(stem+'_depth.pgm',pair['depth']['raw']),
                        'depth_candidate_mask_file':(stem+'_mask.png',mask)}
                saved=dict(result)
                for key,(name,image) in images.items():
                    if not cv2.imwrite(str(args.output/name),image):raise RuntimeError('Cannot save evidence')
                    saved[key]=name
                write_json(args.output/(stem+'.json'),saved)
                print('RGB_ROI_SAVED '+json.dumps(saved),flush=True)

            with (args.output/'observations.jsonl').open('w') as observations:
                while not stopped and time.monotonic()-started<args.seconds:
                    frame=camera.read(); now=time.monotonic()
                    rgb,config,error=color.latest()
                    if error:raise RuntimeError(error)
                    if frame is not None:
                        last_depth=now
                        if rgb is not None:
                            last_pair={'depth':frame,'color':rgb,
                                'host_time_delta':abs(frame['host_monotonic']-rgb['host_monotonic'])}
                    if config is not None and not config_written:
                        write_json(args.output/'session.json',{'source':'REAL_CAMERA','depth':camera.metadata,
                            'color':config,'factory_parameters_float30':mapping.parameters,
                            'synthetic_sdk_preflight':mapping.preflight,'application_depth_range_mm':[600,4000],
                            'registration_mode':0,'mapping_method':'Native SDK PIXEL_D2C for selected original-depth pixel',
                            'selection_method':'User RGB surface reference; fixed original pixel, bounded same-surface neighbor fallback',
                            'compare_previous_selector_on_same_frames':args.compare_baseline,
                            'preselection_note':'Factory pinhole model without additional distortion correction; verified against SDK queries, not physical calibration'})
                        print('RGB_ROI_READY: drag LEFT RGB box, then RIGHT-click intended surface',flush=True)
                        config_written=True
                    if now-last_process>=.12:
                        result=record_base('WAITING_FOR_FRESH_PAIR'); mask=np.zeros_like(last_mask)
                        if drag is not None:
                            result['reason']='RGB_DRAG_IN_PROGRESS'
                        elif pair_is_fresh(last_pair,now):
                            pair=last_pair; depth=pair['depth']; rgb_frame=pair['color']
                            result.update(depth_frame_index=depth['frame_index'],
                                depth_device_timestamp_us=depth['device_timestamp_us'],
                                depth_host_received_utc=depth['host_received_utc'],
                                color_host_received_utc=rgb_frame['host_received_utc'],
                                color_host_read_index=rgb_frame['host_read_index'],
                                host_read_return_time_difference_seconds=pair['host_time_delta'])
                            candidates,mask=reference.candidates(depth['raw'],box,mapping.projection,camera.minimum,camera.maximum)
                            result.update({k:v for k,v in candidates.items() if k not in ('valid','position_m','candidate_points')})
                            if candidates['valid']:
                                result['reason']='NO_CANDIDATE_PASSES_NATIVE_D2C'
                                for point in candidates['candidate_points'][:3]:
                                    sample=checked_sample(point)
                                    if sample is None:continue
                                    if np.linalg.norm(np.asarray(sample['native_sdk_rgb_uv'])-reference.anchor)>reference.rgb_radius_px:continue
                                    if not pair_is_fresh(pair,time.monotonic()):break
                                    reference.bind(point,sample['native_sdk_rgb_uv'])
                                    result.update({k:v for k,v in sample.items() if k!='position_m'})
                                    result['sample_offset_from_reference_depth_px']=reference.sample_offset(point)
                                    result['same_original_depth_pixel']=result['sample_offset_from_reference_depth_px']==0
                                    if reference.accept(depth['frame_index'],now):
                                        result.update(valid=True,position_m=sample['position_m'],reason='SDK_SURFACE_POINT_AVAILABLE')
                                    else:result['reason']='WAITING_FOR_CONSECUTIVE_VALID_DEPTH'
                                    break
                            if args.compare_baseline and reference.anchor is not None:
                                result['comparison_baseline']=baseline_on_frame(depth,now)
                        if not result['valid'] and result['reason']!='WAITING_FOR_CONSECUTIVE_VALID_DEPTH':reference.reject()
                        # Computation can take time: never publish coordinates for an aged pair.
                        if not pair_is_fresh(last_pair,time.monotonic()):
                            result.update(valid=False,position_m=None,reason='STALE_PAIR'); reference.reject(); stable=[]
                            if 'comparison_baseline' in result:result['comparison_baseline']={'valid':False,'reason':'STALE_PAIR'}
                        result['surface_reference']=reference.metadata()
                        if reference.reference is not None and last_pair is not None:
                            ru,rv=reference.reference['depth_pixel_uv'];current=last_pair['depth']['raw']
                            result['reference_current_raw_depth']=int(current[rv,ru])
                            patch=current[max(0,rv-4):rv+5,max(0,ru-4):ru+5]
                            result['reference_patch_shape']=list(patch.shape)
                            result['reference_patch_zero_fraction']=float(np.mean(patch==0))
                        if reference.anchor is not None:
                            statistics['samples']+=1
                            statistics['valid_samples']+=int(result['valid'])
                            statistics['baseline_valid_samples']+=int(result.get('comparison_baseline',{}).get('valid',False))
                        result['availability_statistics']=dict(statistics)
                        result['recorded_at_utc']=utc_now()
                        write_json(args.output/'latest.json',result)
                        observations.write(json.dumps(result,allow_nan=False)+'\n'); observations.flush()
                        last_result=result; measured_pair=last_pair; last_mask=mask; last_process=time.monotonic()
                        if result['valid'] and not autosaved:
                            save(last_pair,result,mask,'first_valid'); autosaved=True

                    pair=frozen if frozen is not None else (measured_pair if last_result and last_result['valid'] else last_pair)
                    if pair is not None and (frozen is not None or pair_is_fresh(pair,time.monotonic())):
                        left=pair['color']['image'].copy(); raw=pair['depth']['raw']
                        right=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                        right[(raw<600)|(raw>4000)]=0
                        if box is not None:
                            x,y,w,h=box; cv2.rectangle(left,(x,y),(x+w-1,y+h-1),(0,255,255),2)
                        if drag is not None:
                            cv2.rectangle(left,tuple(drag[:2]),tuple(drag[2:]),(0,255,255),2)
                        if reference.anchor is not None:
                            cv2.circle(left,tuple(reference.anchor),7,(255,255,0),1)
                        if last_result and last_result['valid'] and pair_is_fresh(measured_pair,time.monotonic()):
                            cv2.drawMarker(left,tuple(last_result['native_sdk_rgb_uv']),(0,255,0),cv2.MARKER_CROSS,19,2)
                            cv2.drawMarker(right,tuple(last_result['depth_pixel_uv']),(255,255,255),cv2.MARKER_CROSS,19,2)
                        cv2.putText(left,'RGB: drag box, RIGHT-click reference surface',(8,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,255,255),1)
                        cv2.putText(right,'DEPTH: black = zero or outside 600-4000 mm',(8,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                        canvas=np.vstack((np.hstack((left,right)),np.zeros((135,1280,3),np.uint8)))
                    else:canvas=np.zeros((615,1280,3),np.uint8)
                    result=last_result or record_base('WAITING_FOR_FRESH_PAIR')
                    if result['valid'] and not pair_is_fresh(measured_pair,time.monotonic()):
                        result=dict(result,valid=False,position_m=None,reason='STALE_PAIR')
                    lines=[STATUS_TEXT.get(result['reason'],result['reason']),
                        'XYZ camera (m; right/down/forward) = '+str(result['position_m']) if result['valid'] else 'XYZ unavailable: no previous coordinates retained',
                        'Reference depth pixel: '+str(reference.metadata()['reference_depth_pixel_uv'])+
                            ' | raw(sample): '+str(result.get('reference_current_raw_depth'))+
                            ' | sample offset (px): '+str(result.get('sample_offset_from_reference_depth_px')),
                        'Current valid samples: '+str(statistics['valid_samples'])+'/'+str(statistics['samples'])+' (availability, not accuracy)',
                        'RIGHT-click: reselect surface | C: clear | S: save | Q: close | Fixed camera + target required']
                    for i,line in enumerate(lines):
                        cv2.putText(canvas,line,(8,501+i*24),cv2.FONT_HERSHEY_SIMPLEX,.46,(0,255,255),1)
                    cv2.imshow(title,canvas); displayed=pair if frozen is not None or pair_is_fresh(pair,time.monotonic()) else None
                    key=cv2.waitKey(1)&255
                    if key in (ord('q'),ord('Q')):break
                    if key in (ord('c'),ord('C'),27):
                        drag=None; frozen=None; select(None,'USER_CLEAR')
                    if key in (ord('s'),ord('S')) and last_result and pair_is_fresh(measured_pair,time.monotonic()):
                        save(measured_pair,last_result,last_mask,'user_snapshot')
                    if args.control.exists():
                        try:
                            command=json.loads(args.control.read_text())
                            if command.get('id') is not None and command['id']!=last_command:
                                last_command=command['id']; action=command.get('action')
                                if action=='stop':stop()
                                elif action=='clear':select(None,'AGENT_DIAGNOSTIC_CLEAR')
                                elif action=='set_roi':select(command['box'],command.get('source','AGENT_DIAGNOSTIC'))
                                elif action=='set_anchor':set_anchor(command['uv'],command.get('source','AGENT_DIAGNOSTIC_REFERENCE'))
                                elif action=='save' and last_result and pair_is_fresh(measured_pair,time.monotonic()):
                                    save(measured_pair,last_result,last_mask,'chat_snapshot')
                        except (OSError,ValueError,KeyError) as error:print('CONTROL_ERROR '+str(error),flush=True)
                    visible=cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
                    if visible>=1:seen=True
                    elif seen:break
                    if now-last_depth>5 or (rgb is None and now-started>8) or (rgb is not None and now-rgb['host_monotonic']>5):
                        raise RuntimeError('Camera stream stalled')
    finally:
        color.close(); cv2.destroyAllWindows()
        closed=dict(last_result or {'source':'REAL_CAMERA'})
        closed.update(valid=False,position_m=None,reason='SESSION_CLOSED',recorded_at_utc=utc_now(),selection=selection)
        write_json(args.output/'latest.json',closed)


if __name__=='__main__':
    main()
