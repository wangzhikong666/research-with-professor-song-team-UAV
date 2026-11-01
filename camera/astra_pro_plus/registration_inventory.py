"""Inspect the active factory calibration and SDK registration support, without enabling D2C."""
import argparse
import ctypes as ct
import json
from pathlib import Path
import cv2
import numpy as np
from depth_camera import DepthCamera


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    with DepthCamera() as camera:
        s=camera.sdk
        s.astra_depth_registration_supported.restype=ct.c_int
        s.astra_depth_registration_supported.argtypes=[]
        s.astra_depth_registration_query.argtypes=[ct.c_int,ct.c_int,ct.c_int,ct.c_uint16,
                                                    ct.c_int,ct.c_int,ct.POINTER(ct.c_int)]
        s.astra_depth_registration_query.restype=ct.c_int
        params=(ct.c_float*30)(); camera.check(s.astra_depth_calibration(params,30))
        values=list(params)
        rotation=np.asarray(values[8:17]).reshape(3,3)
        report=dict(camera.metadata,factory_parameters_float30=values,
                    factory_rotation_field='r2l_r; direction not inferred from field name',
                    factory_rotation_row_major=rotation.tolist(),
                    factory_translation_uninterpreted=values[17:20],
                    rotation_determinant=float(np.linalg.det(rotation)),
                    rotation_orthogonality_max_abs_error=float(np.max(np.abs(rotation.T@rotation-np.eye(3)))),
                    hardware_registration_supported=bool(s.astra_depth_registration_supported()),
                    hardware_registration_enabled=False,color_target_size=[640,480],
                    pixel_query_tests=[])
        for kind,name in [(0,'PIXEL_REGISTRATION'),(1,'PIXEL_D2C_REGISTRATION')]:
            for u,v in [(320,240),(100,100),(500,350)]:
                out=(ct.c_int*3)()
                status=s.astra_depth_registration_query(kind,u,v,1000,640,480,out)
                report['pixel_query_tests'].append({'property':name,'synthetic_input':[u,v,1000],
                    'status':status,'output_uv':list(out)[:2] if status==0 else None,
                    'returned_bytes':out[2],'source':'SYNTHETIC_API_QUERY_NOT_PHYSICAL_MEASUREMENT'})
        frame=None
        for _ in range(120):
            current=camera.read()
            if current is not None:
                frame=current
                if current['frame_index']>=30:
                    break
        if frame is None:
            raise RuntimeError('No depth frame')
        if not cv2.imwrite(str(args.output/'depth_unmirrored.pgm'),frame['raw']):
            raise RuntimeError('Cannot save raw depth')
        report['depth_snapshot']={k:v for k,v in frame.items() if k not in ('raw','host_monotonic')}
        report['scope']='Calibration/capability inventory only; no RGB alignment or geometric accuracy acceptance'
        (args.output/'registration_inventory.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ['hardware_registration_supported',
             'factory_translation_uninterpreted','rotation_determinant','rotation_orthogonality_max_abs_error','pixel_query_tests']},indent=2))


if __name__=='__main__':
    main()
