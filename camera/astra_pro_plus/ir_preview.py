"""Live IR intensity preview for aiming a cardboard target; no RGB or XYZ claim."""
import ctypes as ct
import signal
from pathlib import Path
import time
import cv2
import numpy as np


def main():
    root = Path(__file__).resolve().parents[2]
    sdk = ct.CDLL(str(root/'build/libastra_ir.so'))
    sdk.astra_ir_open.argtypes = [ct.POINTER(ct.c_int)]*4
    sdk.astra_ir_open.restype = ct.c_int
    sdk.astra_ir_read.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.c_uint64)]
    sdk.astra_ir_read.restype = ct.c_int
    sdk.astra_ir_error.argtypes = []
    sdk.astra_ir_error.restype = ct.c_char_p
    sdk.astra_ir_close.argtypes = []
    sdk.astra_ir_close.restype = None
    width, height, fps, mirror = (ct.c_int() for _ in range(4))
    title = 'Astra Pro Plus IR - place cardboard inside yellow box - q to close'
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if sdk.astra_ir_open(ct.byref(width),ct.byref(height),ct.byref(fps),ct.byref(mirror)) != 0:
            raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
        w,h = width.value,height.value
        raw = np.empty((h,w),dtype=np.uint16)
        stamp = ct.c_uint64()
        x,y,rw,rh = (w-w//10)//2,(h-h//10)//2,w//10,h//10
        cv2.namedWindow(title,cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title,960,720)
        cv2.imshow(title,np.zeros((h,w,3),np.uint8))
        print(f'IR LIVE: {w}x{h}, nominal {fps.value} FPS, mirror={bool(mirror.value)}; yellow ROI=({x},{y},{rw},{rh}); no depth stream active',flush=True)
        count = 0
        started = time.monotonic()
        last_frame = started
        seen_window = False
        while not stopping:
            result = sdk.astra_ir_read(raw.ctypes.data,raw.nbytes,ct.byref(stamp))
            if result < 0:
                raise RuntimeError(sdk.astra_ir_error().decode(errors='replace'))
            if result == 0:
                last_frame = time.monotonic()
                # Fixed display-only scale based on observed raw range; not a sensor specification.
                gray = np.clip(raw.astype(float)/1023*255,0,255).astype(np.uint8)
                frame = cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
                cv2.rectangle(frame,(x,y),(x+rw-1,y+rh-1),(0,255,255),2)
                cv2.drawMarker(frame,(w//2,h//2),(0,255,255),cv2.MARKER_CROSS,9,1)
                cv2.putText(frame,'IR ONLY - not RGB or depth',(8,20),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,255),1)
                cv2.putText(frame,'Place flat cardboard face fully inside yellow box',(8,h-28),cv2.FONT_HERSHEY_SIMPLEX,0.43,(0,255,255),1)
                cv2.putText(frame,f'Center IR intensity={int(raw[h//2,w//2])} | q / Esc: close',(8,h-10),cv2.FONT_HERSHEY_SIMPLEX,0.43,(0,255,255),1)
                cv2.imshow(title,frame)
                count += 1
            if cv2.waitKey(1)&255 in (ord('q'),27):
                break
            visible = cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)
            if visible >= 1:
                seen_window = True
            elif seen_window and visible == 0:
                break
            if time.monotonic()-last_frame>10:
                raise RuntimeError('No IR frame for 10 seconds')
        print(f'IR preview closed; received {count} frames over {time.monotonic()-started:.2f} host seconds',flush=True)
    finally:
        sdk.astra_ir_close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
