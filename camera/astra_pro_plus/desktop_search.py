"""Live two-priority desktop search using the Astra Pro Plus color stream."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from depth_camera import DepthCamera
from rgb_roi_depth import in_box
from rgb_roi_xyz import SdkMapping
from surface_reference import SurfaceReference
from uvc_stream import UvcColorStream


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "third_party/desktop-search/object_detection_yolox_2022nov.onnx"
MIN_CONFIDENCE = 0.60
MAX_RGB_DEPTH_DELTA_S = 0.05
COCO_LAPTOP, COCO_MOUSE = 63, 64
LABELS = {COCO_LAPTOP: "第二优先级 · 笔记本", COCO_MOUSE: "第一优先级 · 鼠标"}
COLORS = {COCO_LAPTOP: (255, 193, 83), COCO_MOUSE: (93, 229, 148)}
SEARCH_HINTS = {"both": "优先查看笔记本两侧", "left": "优先查看笔记本左侧",
                "right": "优先查看笔记本右侧"}
WINDOW = "Astra Pro Plus | Desktop Search"


@dataclass(frozen=True)
class Detection:
    category: int
    confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class SurfacePoint:
    rgb_uv: tuple[int, int]
    xyz_m: tuple[float, float, float]


class DepthSurfaceLocator:
    """Measure a fresh visible point near the center of an RGB detection."""

    def __init__(self, camera):
        self.camera = camera
        self.mapping = SdkMapping(camera)
        self.reference = SurfaceReference()

    def locate(self, depth, detection):
        x0, y0, x1, y1 = detection.box
        box = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        self.reference.reset(((x0 + x1) // 2, (y0 + y1) // 2))
        candidates, _ = self.reference.candidates(
            depth["raw"], box, self.mapping.projection, self.camera.minimum, self.camera.maximum
        )
        for point in candidates["candidate_points"]:
            u, v = point["depth_pixel_uv"]
            raw = point["raw_depth"]
            uv = self.mapping.pixel(u, v, raw)
            if max(abs(a - b) for a, b in zip(uv, point["predicted_rgb_uv"])) > 2:
                continue
            if not in_box(uv, box):
                continue
            xyz = self.camera.optical_xyz_m(u, v, raw)
            if np.isfinite(xyz).all():
                return SurfacePoint(tuple(uv), tuple(xyz))
        return None


class DepthSession:
    """Keep color search usable when the optional depth connection fails."""

    def __init__(self):
        self.camera = None
        self.locator = None
        self.error = None
        self.status = "等待配对深度帧"
        try:
            self.camera = DepthCamera()
            self.locator = DepthSurfaceLocator(self.camera)
        except (RuntimeError, OSError, ValueError) as error:
            self.fail("深度连接失败", error)

    def fail(self, status, error):
        self.error = str(error)
        self.status = status + " · 彩色识别继续"
        self.close()
        print(json.dumps({"depth_status": self.status, "depth_error": self.error},
                         ensure_ascii=False), flush=True)

    def read(self):
        if self.camera is None:
            return None
        try:
            return self.camera.read()
        except (RuntimeError, OSError) as error:
            self.fail("深度读取失败", error)
            return None

    def measure(self, depth, detections):
        points = [None] * len(detections)
        if self.camera is None:
            return points
        self.status = "等待配对深度帧"
        if depth is None:
            return points
        if not np.any(depth["raw"]):
            self.status = "深度画面全零 · 请调整拍摄距离和朝向"
            return points
        try:
            points = [self.locator.locate(depth, item) for item in detections]
        except (RuntimeError, OSError) as error:
            self.fail("深度坐标转换失败", error)
            return [None] * len(detections)
        self.status = "XYZ 相机坐标（米）· X右 Y下 Z前"
        if detections and not any(point is not None for point in points):
            self.status = "目标区域深度不可用 · 可停稳相机、调整距离"
        return points

    def close(self):
        camera, self.camera = self.camera, None
        self.locator = None
        if camera is not None:
            camera.close()


class DesktopDetector:
    """Decode the OpenCV Zoo YOLOX export and keep the two COCO classes."""

    def __init__(self, model, confidence):
        self.net = cv2.dnn.readNet(str(model))
        self.confidence = confidence
        grids, strides = [], []
        for stride in (8, 16, 32):
            x, y = np.meshgrid(np.arange(640 // stride), np.arange(640 // stride))
            grids.append(np.column_stack((x.ravel(), y.ravel())))
            strides.append(np.full((x.size, 1), stride))
        self.grid = np.concatenate(grids).astype(np.float32)
        self.stride = np.concatenate(strides).astype(np.float32)

    def detect(self, image):
        height, width = image.shape[:2]
        scale = 640 / max(height, width)
        resized = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))
        padded = np.full((640, 640, 3), 114, np.uint8)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        self.net.setInput(cv2.dnn.blobFromImage(padded, swapRB=True))
        output = self.net.forward()
        if output.shape != (1, len(self.grid), 85):
            raise RuntimeError(f"Unexpected YOLOX output shape {output.shape}")
        rows = output[0]
        categories = rows[:, 5:].argmax(axis=1)
        scores = rows[:, 4] * rows[np.arange(len(rows)), categories + 5]

        detections = []
        for category in LABELS:
            indices = np.flatnonzero((categories == category) & (scores >= self.confidence))
            centers = (rows[indices, :2] + self.grid[indices]) * self.stride[indices]
            sizes = np.exp(rows[indices, 2:4]) * self.stride[indices]
            boxes = np.column_stack((centers - sizes / 2, sizes)) / scale
            confidence = scores[indices]
            # Scores were already filtered inclusively. NMS uses a strict cutoff.
            keep = cv2.dnn.NMSBoxes(boxes.tolist(), confidence.tolist(), 0.0, 0.5)
            for index in np.asarray(keep, dtype=int).ravel():
                x, y, w, h = boxes[index]
                x0, x1 = np.clip(np.rint([x, x + w]), 0, width - 1).astype(int)
                y0, y1 = np.clip(np.rint([y, y + h]), 0, height - 1).astype(int)
                if x1 > x0 and y1 > y0:
                    detections.append(Detection(category, float(confidence[index]),
                                                (int(x0), int(y0), int(x1), int(y1))))
        return detections


class SearchSession:
    def __init__(self, side):
        self.hint = SEARCH_HINTS[side]
        self.state = None
        self.frames = 0
        self.found_frame = None

    def observe(self, detections):
        self.frames += 1
        seen = {item.category for item in detections}
        if self.found_frame is None and COCO_MOUSE in seen:
            self.found_frame = self.frames
        if self.found_frame is not None:
            state, status = "found", "已找到鼠标"
        elif COCO_LAPTOP in seen:
            state, status = "landmark", self.hint
        else:
            state, status = "scan", "继续扫描"
        if state != self.state:
            self.state = state
            print(json.dumps({"frame": self.frames, "state": state, "status": status},
                             ensure_ascii=False), flush=True)
        return status


def draw_frame(frame, detections, status, font, small_font, points=None, depth_status=None):
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    for item in detections:
        color = COLORS[item.category]
        draw.rectangle(item.box, outline=color, width=2)
    for index, item in enumerate(detections):
        x, y, _, _ = item.box
        color = COLORS[item.category]
        lines = [f"{LABELS[item.category]}  {item.confidence:.0%}"]
        if points is not None:
            point = points[index]
            if point is None:
                lines.append("深度不可用")
            else:
                u, v = point.rgb_uv
                draw.line((u - 5, v, u + 5, v), fill=color, width=2)
                draw.line((u, v - 5, u, v + 5), fill=color, width=2)
                px, py, pz = point.xyz_m
                lines.append(f"X {px:+.2f}   Y {py:+.2f}   Z {pz:.2f} m")
        width = int(max(draw.textlength(line, font=small_font) for line in lines)) + 12
        height = 22 * len(lines)
        x = max(0, min(x, image.width - width))
        y = y - height - 1 if y >= 31 + height else max(31, y + 3)
        draw.rectangle((x, y, x + width, y + height), fill=color)
        for row, line in enumerate(lines):
            draw.text((x + 6, y + 11 + row * 22), line, font=small_font,
                      fill=(12, 23, 29), anchor="lm")

    draw.rectangle((0, 0, image.width, 30), fill=(20, 30, 38))
    draw.text((9, 15), status, font=font, fill=(244, 248, 250), anchor="lm")
    if depth_status is not None:
        draw.rectangle((0, image.height - 26, image.width, image.height), fill=(20, 30, 38))
        draw.text((9, image.height - 13), depth_status, font=small_font,
                  fill=(244, 248, 250), anchor="lm")
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def source_frames(args, poll_window, camera=None):
    if args.image:
        image = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"无法读取图片 {args.image}")
        yield image, None
        return
    if args.video:
        capture = cv2.VideoCapture(str(args.video))
        try:
            if not capture.isOpened():
                raise RuntimeError(f"无法打开视频 {args.video}")
            while True:
                ok, image = capture.read()
                if not ok:
                    return
                yield image, None
        finally:
            capture.release()
        return

    stream = UvcColorStream(str(args.device)).start()
    index = 0
    deadline = time.monotonic() + 5
    try:
        while True:
            frame, _, error = stream.latest()
            if error:
                raise RuntimeError(f"Astra 彩色取流失败 ({args.device})：{error}")
            if frame is not None and frame["host_read_index"] != index:
                depth = None
                if camera is not None:
                    depth = camera.read()
                    frame, _, error = stream.latest()
                    if error:
                        raise RuntimeError(f"Astra 彩色取流失败 ({args.device})：{error}")
                    if depth is not None and abs(
                        depth["host_monotonic"] - frame["host_monotonic"]
                    ) > MAX_RGB_DEPTH_DELTA_S:
                        depth = None
                index = frame["host_read_index"]
                yield frame["image"], depth
                deadline = time.monotonic() + 5
            elif time.monotonic() > deadline:
                raise RuntimeError(f"Astra 彩色取流超时 ({args.device})")
            elif poll_window(10):
                return
    finally:
        stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--device", type=Path, help="Astra UVC device (default /dev/video0)")
    source.add_argument("--image", type=Path, help="Process one image")
    source.add_argument("--video", type=Path, help="Process a video file")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--confidence", type=float, default=MIN_CONFIDENCE,
                        help="Detection confidence cutoff (default and minimum: 0.60)")
    parser.add_argument("--side", choices=SEARCH_HINTS, default="both")
    parser.add_argument("--xyz", action="store_true", help="Show camera-relative XYZ using live Astra depth")
    parser.add_argument("--font", type=Path, help="Chinese TTF/OTF font override")
    parser.add_argument("--headless", action="store_true", help="Run without opening a window")
    parser.add_argument("--max-frames", type=int, help="Stop after processing this many frames")
    parser.add_argument("--save-preview", type=Path, help="Save the last annotated frame as PNG")
    args = parser.parse_args()
    if not MIN_CONFIDENCE <= args.confidence <= 1:
        parser.error(f"Confidence must be between {MIN_CONFIDENCE:.2f} and 1")
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("Specify a positive frame limit")
    if args.xyz and (args.image or args.video):
        parser.error("--xyz requires live Astra color and depth")
    if not (args.image or args.video):
        args.device = args.device or Path("/dev/video0")
        if not args.device.exists():
            parser.error(f"找不到 {args.device}，请检查 Astra 彩色设备节点")
    if not args.model.is_file():
        parser.error(f"缺少模型 {args.model}，请按 README 下载 OpenCV Zoo YOLOX 权重")
    if not args.headless and not os.environ.get("DISPLAY"):
        parser.error("当前没有 X11 DISPLAY，请从 Ubuntu 桌面启动或加 --headless")

    def poll_window(delay):
        if args.headless:
            time.sleep(delay / 1000)
            return False
        key = cv2.waitKey(delay) & 0xFF
        if key in (ord("q"), 27):
            return True
        try:
            return cv2.getWindowProperty(WINDOW, cv2.WND_PROP_AUTOSIZE) < 0
        except cv2.error:
            return True  # GTK raises when the user closes the window.

    window_open = False
    frames = None
    depth_session = None
    try:
        cv2.setNumThreads(4)
        detector = DesktopDetector(args.model, args.confidence)
        font_path = args.font or "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        font_index = 0 if args.font else 2
        font = ImageFont.truetype(str(font_path), 18, index=font_index)
        small_font = ImageFont.truetype(str(font_path), 15, index=font_index)
        session = SearchSession(args.side)
        if args.xyz:
            depth_session = DepthSession()
        if not args.headless:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            window_open = True
            cv2.resizeWindow(WINDOW, 800, 600)
            cv2.imshow(WINDOW, draw_frame(np.zeros((480, 640, 3), np.uint8), [],
                                         "等待画面", font, small_font))
        frames = source_frames(args, poll_window, depth_session)
        preview = None
        detections = []
        points = None
        paired_frames = frames_with_xyz = depth_frames_with_data = 0
        for frame, depth in frames:
            detections = detector.detect(frame)
            status = session.observe(detections)
            if depth_session is not None:
                paired_frames += int(depth is not None)
                depth_frames_with_data += int(depth is not None and np.any(depth["raw"]))
                points = depth_session.measure(depth, detections)
                frames_with_xyz += int(any(point is not None for point in points))
            if window_open or args.save_preview:
                preview = draw_frame(frame, detections, status, font, small_font, points,
                                     None if depth_session is None else depth_session.status)
            if window_open:
                cv2.imshow(WINDOW, preview)
                if poll_window(1):
                    break
            if args.max_frames == session.frames:
                break
        if args.image and window_open and args.max_frames is None:
            while not poll_window(30):
                pass
        if not session.frames and (args.image or args.video or args.headless):
            raise RuntimeError("没有收到可用画面")
        if args.save_preview and preview is not None:
            ok, encoded = cv2.imencode(".png", preview)
            if not ok:
                raise RuntimeError("保存标注画面失败")
            encoded.tofile(args.save_preview)
        last_detections = [{"label": LABELS[item.category],
                            "confidence": round(item.confidence, 3), "box": item.box}
                           for item in detections]
        summary = {"frames_processed": session.frames, "found_frame": session.found_frame,
                   "last_detections": last_detections}
        if args.xyz:
            summary.update(coordinate_frame="camera_depth_optical_sdk",
                           paired_frames=paired_frames, frames_with_xyz=frames_with_xyz,
                           depth_frames_with_data=depth_frames_with_data,
                           depth_status=depth_session.status, depth_error=depth_session.error)
            for item, point in zip(last_detections, points or []):
                item["xyz_m"] = None if point is None else point.xyz_m
                item["sample_rgb_uv"] = None if point is None else point.rgb_uv
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except (RuntimeError, OSError, ValueError, cv2.error) as error:
        print(f"桌面搜索：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if frames is not None:
            frames.close()
        if depth_session is not None:
            depth_session.close()
        if window_open:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
