"""Single-depth session using the pinned official OpenNI bridge; no RGB."""
import ctypes as ct
from datetime import datetime, timezone
from pathlib import Path
import time
import numpy as np


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class DepthCamera:
    def __init__(self,registered=False):
        self.registered=bool(registered)
        root = Path(__file__).resolve().parents[2]
        self.sdk = s = ct.CDLL(str(root/'build/libastra_ir.so'))
        s.astra_point_open.argtypes=[ct.POINTER(ct.c_int),ct.c_int]
        s.astra_point_open.restype=ct.c_int
        s.astra_registered_open.argtypes=[ct.POINTER(ct.c_int),ct.c_int]
        s.astra_registered_open.restype=ct.c_int
        s.astra_scene_read.argtypes=[ct.POINTER(ct.c_int),ct.c_void_p,ct.c_int,
                                    ct.POINTER(ct.c_uint64),ct.POINTER(ct.c_int)]
        s.astra_scene_read.restype=ct.c_int
        s.astra_depth_calibration.argtypes=[ct.POINTER(ct.c_float),ct.c_int]
        s.astra_depth_calibration.restype=ct.c_int
        s.astra_depth_distortion_state.argtypes=[ct.POINTER(ct.c_uint64)]
        s.astra_depth_distortion_state.restype=ct.c_int
        s.astra_depth_to_world.argtypes=[ct.c_float]*3+[ct.POINTER(ct.c_float)]
        s.astra_depth_to_world.restype=ct.c_int
        s.astra_ir_error.restype=ct.c_char_p
        s.astra_ir_close.argtypes=[]
        s.astra_ir_close.restype=None
        try:
            values=(ct.c_int*13)()
            self.check((s.astra_registered_open if self.registered else s.astra_point_open)(values,13))
            self.width,self.height,self.fps,self.format,mirror,self.minimum,self.maximum=list(values)[:7]
            if (self.width,self.height,self.format,mirror)!=(640,480,100,0):
                raise RuntimeError('Requires actual 640x480 DEPTH_1_MM and mirror off')
            if int(values[12])!=(1 if self.registered else 0):
                raise RuntimeError('Actual registration mode differs from request')
            params=(ct.c_float*30)()
            self.check(s.astra_depth_calibration(params,30))
            factory=np.array(list(params),dtype=float)
            if not np.isfinite(factory).all() or min(factory[0],factory[1])<=0:
                raise RuntimeError('Factory calibration unavailable')
            state=ct.c_uint64()
            state_status=s.astra_depth_distortion_state(ct.byref(state))
            sdk_projection=None
            if not self.registered:
                left,right=self.native_xyz(0,self.height/2,1000),self.native_xyz(self.width-1,self.height/2,1000)
                top,bottom=self.native_xyz(self.width/2,0,1000),self.native_xyz(self.width/2,self.height-1,1000)
                fx=(self.width-1)*1000/(right[0]-left[0])
                fy=(self.height-1)*1000/(top[1]-bottom[1])
                sdk_projection=[fx,fy,-left[0]*fx/1000,top[1]*fy/1000]
            self.metadata={
                'source':'REAL_CAMERA','started_utc':utc_now(),'width':self.width,'height':self.height,
                'nominal_fps':self.fps,'pixel_format_enum':self.format,
                'millimeters_per_raw_unit':1,'mirroring':False,'registration_mode':int(values[12]),
                'factory_depth_intrinsics_fx_fy_cx_cy':factory[:4].tolist(),
                'factory_depth_distortion_k1_k2_p1_p2_k3':factory[20:25].tolist(),
                'sdk_effective_projection_fx_fy_cx_cy':sdk_projection,
                'distortion_state':{'status':state_status,'value':int(state.value) if state_status==0 else None},
                'coordinate_frame':'UNASSIGNED_REGISTERED_DEPTH' if self.registered else 'camera_depth_optical_sdk',
                'axes':'Pixel mapping diagnostic only' if self.registered else 'right, down, forward',
                'coordinate_method':'Not provided for registered pixels' if self.registered else 'SDK native conversion with Y sign reversed',
                'pixel_domain':'SDK_DEPTH_TO_COLOR_OUTPUT' if self.registered else 'UNREGISTERED_DEPTH',
                'registered_z_semantics':'SDK output; metric frame not independently verified' if self.registered else None,
                'geometry_verified':False,'physical_accuracy_verified':False,
                'geometry_status':'SDK_MODEL_FACTORY_PRINCIPAL_DIFFERS_DISTORTION_STATE_UNVERIFIED',
                'rgb_used':False,
            }
            self.buffer=np.empty((self.height,self.width),np.uint16)
        except Exception:
            self.close()
            raise

    def check(self,status):
        if status!=0:
            raise RuntimeError(self.sdk.astra_ir_error().decode(errors='replace'))

    def native_xyz(self,u,v,raw):
        if self.registered:
            raise RuntimeError('Do not use raw-depth XYZ converter on registered color-grid pixels')
        values=(ct.c_float*3)()
        self.check(self.sdk.astra_depth_to_world(u,v,raw,values))
        return [float(x) for x in values]

    def optical_xyz_m(self,u,v,raw):
        x,y,z=self.native_xyz(u,v,raw)
        return [x/1000,-y/1000,z/1000]

    def read(self):
        kind,index,stamp=ct.c_int(),ct.c_int(),ct.c_uint64()
        status=self.sdk.astra_scene_read(ct.byref(kind),self.buffer.ctypes.data,self.buffer.nbytes,
                                         ct.byref(stamp),ct.byref(index))
        if status==1:
            return None
        self.check(status)
        if kind.value!=0:
            raise RuntimeError('Unexpected non-depth stream')
        return {'raw':self.buffer.copy(),'frame_index':index.value,'device_timestamp_us':stamp.value,
                'host_received_utc':utc_now(),'host_monotonic':time.monotonic()}

    def close(self):
        self.sdk.astra_ir_close()

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
