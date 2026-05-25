#!/usr/bin/env python3
"""WebRTC camera stream with YOLO overlay and reduced detection frequency.

Usage:
  pip install flask aiortc opencv-python av picamera2 ultralytics
  python cam_stream2.py

Then from browser:
  1) Create offer in JS via RTCPeerConnection
  2) POST { sdp, type } to http://<rpi-ip>:8082/offer
  3) Receive { sdp, type } answer and setRemoteDescription
"""

import asyncio
import cv2
import numpy as np
import threading
import time
from flask import Flask, request, jsonify
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from picamera2 import Picamera2
import os
from ultralytics import YOLO

app = Flask(__name__)
pcs = set()

# Global event loop for WebRTC connections
loop = None
loop_thread = None

def setup_event_loop():
    global loop, loop_thread
    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()
    
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()
    time.sleep(0.5)  # Give loop thread time to start
    print("✓ Global asyncio event loop started")

camera = None
camera_lock = threading.Lock()
yolo_model = None
model_lock = threading.Lock()
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
CAMERA_FPS = 30
DETECTION_INTERVAL = 16  # Reduced from 8 to cut YOLO processing frequency in half
MODEL_PATH = os.environ.get(
    'YOLO_MODEL_PATH',
    os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.pt'))
)

last_detection_count = 0
last_detection_boxes = []


def init_camera():
    global camera
    if camera is not None:
        return camera

    camera = Picamera2()
    config = camera.create_video_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT)})
    camera.configure(config)
    camera.start()
    print("✓ Shared Picamera2 instance started")
    return camera


def init_yolo_model():
    global yolo_model
    if yolo_model is not None:
        return yolo_model

    try:
        if not os.path.exists(MODEL_PATH):
            print(f"⚠ YOLO model not found at {MODEL_PATH}, skipping detection")
            return None
        yolo_model = YOLO(MODEL_PATH)
        print(f"✓ YOLO model loaded from {MODEL_PATH}")
        return yolo_model
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        return None


def normalize_frame(frame):
    if frame is None:
        return None
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    if frame.shape[2] == 1:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    return frame


def draw_detection_boxes(frame, boxes):
    for x1, y1, x2, y2, conf, cls_name in boxes:
        color = (0, 255, 0)
        if cls_name:
            cn = str(cls_name).strip().lower()
            if 'defect' in cn or 'defective' in cn or 'bad' in cn:
                color = (0, 0, 255)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{cls_name or 'Mango'} {conf:.2f}"
        cv2.putText(frame, label, (x1, max(20, y1 - 10)),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame


def detect_frame(frame):
    global last_detection_count, last_detection_boxes
    frame = normalize_frame(frame)
    if frame is None or yolo_model is None:
        last_detection_boxes = []
        last_detection_count = 0
        return frame, []

    try:
        with model_lock:
            results = yolo_model(frame, verbose=False, conf=0.5)

        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            detections = []
            if boxes is not None and len(boxes) > 0:
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls_name = None
                    try:
                        if hasattr(box, 'cls') and box.cls is not None:
                            cls_idx = int(box.cls[0])
                            cls_name = yolo_model.names.get(cls_idx, str(cls_idx)) if hasattr(yolo_model, 'names') else str(cls_idx)
                    except Exception:
                        cls_name = None
                    detections.append((x1, y1, x2, y2, conf, cls_name))
            last_detection_boxes = detections
            last_detection_count = len(detections)
            print(f"✓ YOLO detections: {last_detection_count}")
            frame = draw_detection_boxes(frame, last_detection_boxes)
            return frame, last_detection_boxes

        last_detection_boxes = []
        last_detection_count = 0
        return frame, []
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        last_detection_boxes = []
        last_detection_count = 0
        return frame, []


class CameraTrack(VideoStreamTrack):
    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0
        self.camera_available = False

        try:
            init_camera()
            init_yolo_model()
            self.camera_available = True
            print("✓ Camera initialized for WebRTC via shared Picamera2")
        except Exception as e:
            print(f"⚠ picamera2 error: {e}")
            self.camera_available = False

        if not self.camera_available:
            print("⚠ Camera not available - will stream black frames as fallback")

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        if self.camera_available:
            try:
                with camera_lock:
                    frame = camera.capture_array()
                frame = normalize_frame(frame)
                frame = cv2.flip(frame, 0)

                do_detect = (self.frame_count % DETECTION_INTERVAL) == 0  # Run YOLO every 8 frames
                if do_detect:
                    frame, _ = detect_frame(frame)
                else:
                    frame = draw_detection_boxes(frame, last_detection_boxes)

                self.frame_count += 1
            except Exception as e:
                print(f"⚠ Camera read error: {e}")
                frame = 255 * np.zeros((self.height, self.width, 3), np.uint8)
        else:
            frame = 255 * np.zeros((self.height, self.width, 3), np.uint8)

        try:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame
        except Exception as e:
            print(f"⚠ Frame conversion error: {e}")
            black = np.zeros((self.height, self.width, 3), np.uint8)
            video_frame = VideoFrame.from_ndarray(black, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame


@app.route('/offer', methods=['POST'])
def offer():
    global loop
    if loop is None:
        print("❌ Event loop not initialized")
        return jsonify({'error': 'Server not ready'}), 503
    
    # Set the global event loop as current for this request thread
    # so aiortc can find it when creating RTCPeerConnection
    asyncio.set_event_loop(loop)
    
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'error': 'Missing SDP offer'}), 400

    try:
        offer_desc = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            print('ICE state:', pc.iceConnectionState)
            if pc.iceConnectionState == 'failed':
                asyncio.run_coroutine_threadsafe(pc.close(), loop)

        camera_track = CameraTrack(width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS)
        pc.addTrack(camera_track)

        async def run():
            await pc.setRemoteDescription(offer_desc)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return pc.localDescription

        # Schedule the async work on the global event loop and wait for result
        future = asyncio.run_coroutine_threadsafe(run(), loop)
        local_desc = future.result(timeout=5)

        return jsonify({'sdp': local_desc.sdp, 'type': local_desc.type})
    except Exception as e:
        print(f"❌ WebRTC offer error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/status')
def status():
    return jsonify({
        'model_loaded': yolo_model is not None,
        'model_path': MODEL_PATH,
        'last_detection_count': last_detection_count,
        'resolution': f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
        'detection_interval': DETECTION_INTERVAL,
    })


@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC cam_stream2 running', 'endpoint': '/offer'})


if __name__ == '__main__':
    print("Starting Mango Sorter cam_stream2 on port 8082...")
    print("Access endpoint: http://0.0.0.0:8082/offer")
    setup_event_loop()
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
