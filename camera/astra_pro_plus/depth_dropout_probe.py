"""Short fixed-scene raw-depth dropout test, optionally with concurrent UVC RGB."""
import argparse
import csv
import json
from pathlib import Path
import time
import cv2
import numpy as np
from depth_camera import DepthCamera,utc_now
from point_quality import assess_pixel
from uvc_stream import UvcColorStream


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=int,nargs=2,required=True)
    parser.add_argument('--control-pixel',type=int,nargs=2)
    parser.add_argument('--seconds',type=float,default=15)
    parser.add_argument('--with-rgb',action='store_true')
    args=parser.parse_args()
    if not 0<args.seconds<=30:parser.error('seconds must be 0..30')
    u,v=args.reference
    if not 5<=u<635 or not 5<=v<475:parser.error('reference must allow a 9x9 patch')
    args.output.mkdir(parents=True,exist_ok=False)
    color=UvcColorStream().start() if args.with_rgb else None
    name='Astra raw depth dropout diagnostic - '+('RGB ON' if color else 'RGB OFF')
    rows=[];saved=set();control=args.control_pixel;started=None;last_index=None;gaps=0
    try:
        with DepthCamera() as camera:
            warmed=time.monotonic();cv2.namedWindow(name,cv2.WINDOW_AUTOSIZE)
            with (args.output/'samples.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=['host_received_utc','frame_index','raw_reference',
                    'reference_quality_valid','reference_patch_zero_fraction','control_raw','whole_zero_fraction'])
                writer.writeheader()
                while time.monotonic()-warmed<args.seconds+12:
                    frame=camera.read()
                    if frame is None:continue
                    now=time.monotonic();raw=frame['raw']
                    if color:
                        rgb,config,error=color.latest()
                        if error:raise RuntimeError(error)
                        if rgb is None and now-warmed<8:continue
                        if rgb is None or now-rgb['host_monotonic']>.5:raise RuntimeError('RGB stalled during concurrent test')
                    if now-warmed<2:continue
                    if started is None:started=now
                    if now-started>=args.seconds:break
                    if control is None:
                        valid=((raw>=600)&(raw<=4000)).astype(np.uint8)
                        valid[:100]=0;valid[210:]=0;valid[:,:360]=0;valid[:,550:]=0
                        distance=cv2.distanceTransform(valid,cv2.DIST_L2,3)
                        cy,cx=np.unravel_index(np.argmax(distance),distance.shape)
                        if distance[cy,cx]<3:raise RuntimeError('No supported background control point')
                        control=[int(cx),int(cy)]
                    if last_index is not None:gaps+=max(0,frame['frame_index']-last_index-1)
                    last_index=frame['frame_index'];quality=assess_pixel(raw,u,v,1,600,4000,camera.minimum,camera.maximum)
                    row={'host_received_utc':frame['host_received_utc'],'frame_index':frame['frame_index'],
                         'raw_reference':int(raw[v,u]),'reference_quality_valid':int(quality['valid']),
                         'reference_patch_zero_fraction':float(np.mean(raw[v-4:v+5,u-4:u+5]==0)),
                         'control_raw':int(raw[control[1],control[0]]),'whole_zero_fraction':float(np.mean(raw==0))}
                    writer.writerow(row);rows.append(row)
                    kind='zero' if row['raw_reference']==0 else 'nonzero'
                    if kind not in saved:
                        if not cv2.imwrite(str(args.output/(kind+'_raw_depth.pgm')),raw):
                            raise RuntimeError('Cannot save original depth evidence')
                        saved.add(kind)
                    preview=cv2.applyColorMap(np.clip(raw.astype(float)/2000*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)
                    preview[raw==0]=0;cv2.rectangle(preview,(u-4,v-4),(u+4,v+4),(255,255,255),1)
                    cv2.drawMarker(preview,tuple(control),(0,255,255),cv2.MARKER_CROSS,15,1)
                    cv2.putText(preview,f'RGB {"ON" if color else "OFF"}: {now-started:.1f}/{args.seconds:.0f}s Zraw={raw[v,u]}',
                                (8,25),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),1)
                    cv2.imshow(name,preview)
                    if cv2.waitKey(1)&255==ord('q'):break
            if not rows:raise RuntimeError('No measured frames')
            report={'source':'REAL_CAMERA','started_utc':rows[0]['host_received_utc'],'finished_utc':utc_now(),
                'depth':camera.metadata,'concurrent_rgb':bool(color),'reference_depth_uv':args.reference,
                'control_depth_uv':control,'warmup_seconds':2,'requested_measurement_seconds':args.seconds,
                'measured_frames':len(rows),'sdk_frame_index_gaps':gaps,
                'reference_zero_fraction':float(np.mean([r['raw_reference']==0 for r in rows])),
                'reference_quality_valid_fraction':float(np.mean([r['reference_quality_valid'] for r in rows])),
                'reference_patch_all_zero_fraction':float(np.mean([r['reference_patch_zero_fraction']==1 for r in rows])),
                'control_nonzero_fraction':float(np.mean([r['control_raw']>0 for r in rows])),
                'whole_all_zero_frames':sum(r['whole_zero_fraction']==1 for r in rows),
                'note':'Sequential fixed-scene comparison, not accuracy measurement or synchronized RGB-D capture'}
            (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:
        if color:color.close()
        cv2.destroyAllWindows()


if __name__=='__main__':main()
