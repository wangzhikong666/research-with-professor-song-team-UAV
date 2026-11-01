"""Latest UVC color frame for a static RGB/depth learning preview."""
import threading
import time
import cv2
from depth_camera import utc_now


class UvcColorStream:
    def __init__(self,device='/dev/video0'):
        self.device=device; self.lock=threading.Lock(); self.stopping=threading.Event()
        self.frame=None; self.error=None; self.config=None
        self.worker=threading.Thread(target=self._run,daemon=True)

    def start(self):
        self.worker.start()
        return self

    def _run(self):
        cap=cv2.VideoCapture(self.device,cv2.CAP_V4L2)
        try:
            if not cap.isOpened():raise RuntimeError('Cannot open UVC color device')
            cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
            cap.set(cv2.CAP_PROP_FPS,30)
            config={'device':self.device,'width':int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    'height':int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    'nominal_fps':cap.get(cv2.CAP_PROP_FPS),
                    'transport_fourcc':int(cap.get(cv2.CAP_PROP_FOURCC)),
                    'decoded_format':'BGR uint8','software_flip':False}
            if (config['width'],config['height'])!=(640,480):raise RuntimeError('Requires actual 640x480 RGB')
            with self.lock:self.config=config
            index=0
            while not self.stopping.is_set():
                ok,image=cap.read()
                if not ok or image is None or image.shape!=(480,640,3):raise RuntimeError('UVC read/shape failed')
                index+=1
                frame={'image':image,'host_monotonic':time.monotonic(),'host_received_utc':utc_now(),'host_read_index':index}
                with self.lock:self.frame=frame
        except Exception as error:
            with self.lock:self.error=str(error)
        finally:cap.release()

    def latest(self):
        with self.lock:return self.frame,self.config,self.error

    def close(self):
        self.stopping.set(); self.worker.join(timeout=2)
