#!/usr/bin/env python3
"""WebRTC camera stream with shared YOLO detector and status classification."""

import asyncio
import cv2
import numpy as np
import threading
import time
import os
import zmq
from flask import Flask, request, jsonify
from flask_cors import CORS
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from picamera2 import Picamera2

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"]}})
pcs = set()

# Global state
loop = None
loop_thread = None
camera = None
camera_lock = threading.Lock()

CAMERA_WIDTH = 416
CAMERA_HEIGHT = 312
CAMERA_FPS = 12

latest_frame = None
latest_frame_lock = threading.Lock()
frame_is_new = False

last_detections = []
last_multi_detection = False

detection_cache_lock = threading.Lock()

yolo_model = None

zmq_context = None
mango_publisher = None
DETECTION_PORT = 5555

DETECTION_INTERVAL = 0.03


def setup_event_loop():
    global loop, loop_thread
    if loop is not None:
        return

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=_run_event_loop, daemon=True)
    loop_thread.start()
    time.sleep(0.3)
    print("✓ Global asyncio event loop started")


def _run_event_loop():
    global loop
    asyncio.set_event_loop(loop)
    loop.run_forever()


def load_yolo_model():
    global yolo_model
    try:
        from ultralytics import YOLO
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.onnx'))
        if not os.path.exists(model_path):
            print(f"⚠ YOLO model not found at {model_path}")
            yolo_model = None
            return

        yolo_model = YOLO(model_path, task='detect')
        print(f"✓ YOLO model loaded from {model_path}")
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        yolo_model = None


def init_camera():
    global camera
    if camera is not None:
        return camera
    try:
        camera = Picamera2()
        config = camera.create_video_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"})
        camera.configure(config)
        camera.start()
        print(f"✓ Camera initialized: {CAMERA_WIDTH}x{CAMERA_HEIGHT} @ {CAMERA_FPS}fps")
        return camera
    except Exception as e:
        print(f"⚠ Camera init failed: {e}")
        camera = None
        return None


def get_color_and_status(cls_name):
    """Map the model's exact class name to (BGR color, is_defective, is_not_carabao).

    Model classes: 'Defective', 'Not Defective', 'Not Carabao Mango'. These three are
    distinct categories, so match the exact names instead of guessing from substrings
    (the old substring logic collapsed 'Not Defective' into the 'not carabao' bucket).
    """
    cn = str(cls_name).strip().lower()

    if cn == 'defective':
        return (0, 0, 255), True, False    # Red (BGR) — defective
    if cn == 'not carabao mango':
        return (0, 255, 255), False, True  # Yellow (BGR) — wrong variety (reject)
    if cn == 'not defective':
        return (0, 255, 0), False, False   # Green (BGR) — good carabao mango

    # Fallback for any unexpected label
    if 'carabao' in cn:
        return (0, 255, 255), False, True
    if 'not' not in cn and any(k in cn for k in ['defect', 'bad', 'damaged', 'rotten']):
        return (0, 0, 255), True, False
    return (0, 255, 0), False, False       # default: treat as good


def run_detection(frame):
    if yolo_model is None:
        return []
    try:
        results = yolo_model(frame, verbose=False, conf=0.4, iou=0.45, imgsz=480)
        detections = []
        if results and len(results) > 0:
            for box in results[0].boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls_idx = int(box.cls[0])
                cls_name = yolo_model.names.get(cls_idx, 'unknown') if hasattr(yolo_model, 'names') else str(cls_idx)
                detections.append((x1, y1, x2, y2, conf, cls_name))
        return detections
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        return []


def draw_detection_boxes(frame, boxes):
    for x1, y1, x2, y2, conf, cls_name in boxes:
        color, is_defective, is_not_carabao = get_color_and_status(cls_name)
        if is_defective:
            label = f"DEFECTIVE {conf:.2f}"
        elif is_not_carabao:
            label = f"NOT CARABAO {conf:.2f}"
        else:
            label = f"NOT DEFECTIVE {conf:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(15, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame


def background_capture():
    global latest_frame, frame_is_new
    print("✓ Background capture thread started")

    while True:
        try:
            if camera is None:
                time.sleep(0.1)
                continue

            if not camera_lock.acquire(blocking=False):
                time.sleep(0.01)
                continue
            try:
                frame = camera.capture_array()
            finally:
                camera_lock.release()

            if frame is None or frame.size == 0:
                time.sleep(0.01)
                continue

            clean_frame = np.ascontiguousarray(frame).copy()
            with latest_frame_lock:
                latest_frame = clean_frame
                frame_is_new = True

            time.sleep(1.0 / CAMERA_FPS)
        except Exception as e:
            print(f"⚠ Capture error: {e}")
            time.sleep(0.1)


def background_detect():
    global last_detections, last_multi_detection, frame_is_new
    print("✓ Background detection thread started")

    while True:
        try:
            frame = None
            with latest_frame_lock:
                if frame_is_new and latest_frame is not None:
                    frame = latest_frame.copy()
                    frame_is_new = False

            if frame is None:
                time.sleep(0.01)
                continue

            detection_boxes = run_detection(frame)
            if not detection_boxes:
                with detection_cache_lock:
                    last_detections = []
                    last_multi_detection = False
                time.sleep(DETECTION_INTERVAL)
                continue

            is_defective = False
            for _, _, _, _, _, cls_name in detection_boxes:
                _, det_defective, _ = get_color_and_status(cls_name)
                if det_defective:
                    is_defective = True
                    break

            with detection_cache_lock:
                last_detections = detection_boxes
                last_multi_detection = len(detection_boxes) > 1

            if mango_publisher is not None:
                payload = {
                    'detections': [
                        {'x1': int(x1), 'y1': int(y1), 'x2': int(x2), 'y2': int(y2), 'confidence': float(conf), 'class': str(cls_name)}
                        for x1, y1, x2, y2, conf, cls_name in detection_boxes
                    ],
                    'is_defective': is_defective,
                    'multi_detection': len(detection_boxes) > 1,
                    'timestamp': time.time()
                }
                try:
                    mango_publisher.send_json(payload, flags=zmq.NOBLOCK)
                except zmq.Again:
                    pass

            time.sleep(DETECTION_INTERVAL)
        except Exception as e:
            print(f"⚠ Detection thread error: {e}")
            time.sleep(0.1)


class CameraTrack(VideoStreamTrack):
    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps

    async def recv(self):
        await asyncio.sleep(1.0 / self.fps)
        pts, time_base = await self.next_timestamp()

        with latest_frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None

        if frame is None:
            frame = np.zeros((self.height, self.width, 3), np.uint8)

        with detection_cache_lock:
            frame = draw_detection_boxes(frame, list(last_detections))

        try:
            frame = np.ascontiguousarray(frame)
            # Picamera2 capture_array() returns BGR despite RGB888 format label;
            # convert to RGB so VideoFrame rgb24 displays correct colors.
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame
        except Exception as e:
            print(f"⚠ WebRTC frame conversion error: {e}")
            black = np.zeros((self.height, self.width, 3), np.uint8)
            video_frame = VideoFrame.from_ndarray(black, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame


@app.route('/offer', methods=['POST'])
def offer():
    if loop is None:
        return jsonify({'success': False, 'error': 'Server not ready'}), 503

    data = request.get_json() or {}
    sdp = data.get('sdp')
    sdp_type = data.get('type')

    if not sdp or not sdp_type:
        return jsonify({'success': False, 'error': 'Missing SDP offer'}), 400

    async def handle_offer():
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            if pc.iceConnectionState in ['failed', 'disconnected', 'closed']:
                asyncio.run_coroutine_threadsafe(pc.close(), loop)
                pcs.discard(pc)

        pc.addTrack(CameraTrack())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    try:
        future = asyncio.run_coroutine_threadsafe(handle_offer(), loop)
        local_desc = future.result(timeout=15)
        return jsonify({'sdp': local_desc.sdp, 'type': local_desc.type})
    except Exception as e:
        print(f"⚠ WebRTC offer error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/detection', methods=['GET'])
def detection():
    with detection_cache_lock:
        return jsonify({
            'multi_detection': last_multi_detection,
            'detection_count': len(last_detections),
            'detections': [
                {'x1': int(x1), 'y1': int(y1), 'x2': int(x2), 'y2': int(y2), 'confidence': float(conf), 'class': str(cls_name)}
                for x1, y1, x2, y2, conf, cls_name in last_detections
            ]
        })


@app.route('/status', methods=['GET'])
def status():
    return jsonify({'stream_running': True, 'resolution': f'{CAMERA_WIDTH}x{CAMERA_HEIGHT}', 'fps': CAMERA_FPS})


@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC camera stream running', 'endpoint': '/offer'})


if __name__ == '__main__':
    try:
        zmq_context = zmq.Context()
        mango_publisher = zmq_context.socket(zmq.PUB)
        mango_publisher.setsockopt(zmq.SNDHWM, 1)
        mango_publisher.bind(f"tcp://127.0.0.1:{DETECTION_PORT}")
        print(f"✓ ZMQ detection publisher started on tcp://127.0.0.1:{DETECTION_PORT}")
    except Exception as e:
        print(f"⚠ Failed to initialize ZMQ: {e}")
        mango_publisher = None

    load_yolo_model()
    init_camera()

    threading.Thread(target=background_capture, daemon=True).start()
    threading.Thread(target=background_detect, daemon=True).start()

    setup_event_loop()
    print('✅ Starting HTTP server on http://127.0.0.1:8082')
    print('✅ HTTP routes available: POST /offer, GET /detection, GET /status')
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
