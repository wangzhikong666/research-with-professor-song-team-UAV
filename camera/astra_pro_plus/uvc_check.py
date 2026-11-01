"""Check the real UVC color channel using existing OpenCV/V4L2 permissions."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--fourcc', choices=('MJPG', 'YUYV'), default='MJPG')
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error('--seconds must be in (0, 3600]')
    args.output.mkdir(parents=True, exist_ok=False)
    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open {args.device}; check passthrough and device ACL')
    frames, failures, times, last = 0, 0, [], None
    started = datetime.now(timezone.utc).isoformat()
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_FPS, args.fps)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        config = {
            'width': cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            'height': cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
            'nominal_fps': cap.get(cv2.CAP_PROP_FPS),
            'transport_fourcc': ''.join(chr((fourcc >> (8*i)) & 255) for i in range(4)),
            'decoded_format': 'BGR uint8',
        }
        print(json.dumps(config), flush=True)
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            ok, frame = cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                failures += 1
                if failures >= 10:
                    raise RuntimeError('10 color reads failed; stopping')
                continue
            frames += 1
            times.append(now)
            last = frame
            if args.preview:
                cv2.imshow('Astra Pro Plus UVC color (q to close)', frame)
                if cv2.waitKey(1) & 255 in (ord('q'), 27):
                    break
        elapsed = time.monotonic() - start
        if last is None:
            raise RuntimeError('No real color frame received')
        if not cv2.imwrite(str(args.output / 'color.png'), last):
            raise RuntimeError('Failed to save color.png')
        report = {
            'source': 'REAL_CAMERA', 'device': args.device, 'started_utc': started,
            'opencv': cv2.__version__, 'config': config,
            'actual_frame_shape': list(last.shape), 'frames': frames, 'read_failures': failures,
            'elapsed_seconds': elapsed,
            'observed_fps': (frames-1)/(times[-1]-times[0]) if frames > 1 else None,
            'maximum_host_frame_interval_seconds': max((b-a for a,b in zip(times,times[1:])), default=None),
            'timestamps': 'Host monotonic read-return times; NOT sensor exposure timestamps',
            'rate_caution': 'Decoded read-return count is NOT verified sensor FPS; corrupt or repeated frames may be counted',
            'acceptance': 'RAW_FRAMES_RECEIVED_IMAGE_QUALITY_REQUIRES_REVIEW',
            'rgb_depth_alignment_verified': False,
            'hardware_synchronized_with_depth': False,
        }
        (args.output / 'color_report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2), flush=True)
    except Exception as error:
        (args.output / 'failure_report.json').write_text(json.dumps({
            'source': 'REAL_CAMERA', 'started_utc': started,
            'error': str(error), 'frames_received': frames,
            'read_failures': failures, 'acceptance': 'FAILED',
        }, indent=2)+'\n')
        raise
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
