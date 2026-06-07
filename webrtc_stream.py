#!/usr/bin/env python3
"""WebRTC camera stream with shared YOLO detector.

Captures frames and streams them via WebRTC to browser.
YOLO detections are handled by yolo_detector_shared.py (shared with servotest).

Usage:
  pip install flask flask-cors aiortc opencv-python av picamera2 ultralytics
  python webrtc_stream.py

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
import sys
import os
import zmq
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from picamera2 import Picamera2

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"]}})
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

CAMERA_WIDTH = 480
CAMERA_HEIGHT = 360
CAMERA_FPS = 30

# ZMQ detection publisher (for servotest to consume)
zmq_context = None
detection_publisher = None
DETECTION_PORT = 5555

# Detection state cache for /api/detections endpoint
last_detections = []
last_multi_detection = False
detection_cache_lock = threading.Lock()

# Latest captured frame (shared between background thread and WebRTC recv)
latest_frame = None
latest_frame_lock = threading.Lock()

# Background detection runs at this interval (seconds) — ~10 Hz
DETECTION_INTERVAL = 0.1

# YOLO model (loaded once at startup, shared across all connections)
yolo_model = None

def load_yolo_model():
    """Load YOLO model globally at startup (only once)."""
    global yolo_model
    print("Loading YOLO model...")
    try:
        from ultralytics import YOLO
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.pt'))
        if not os.path.exists(model_path):
            print(f"⚠ YOLO model not found at {model_path}")
            yolo_model = None
        else:
            yolo_model = YOLO(model_path)
            print(f"✓ YOLO model loaded from {model_path}")
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        yolo_model = None

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


def run_detection(frame):
    """Run YOLO detection on frame. Returns list of (x1,y1,x2,y2,conf,cls_name)."""
    if yolo_model is None:
        return []
    try:
        results = yolo_model(frame, verbose=False, conf=0.6, imgsz=320)
        detections = []
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            if boxes is not None and len(boxes) > 0:
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls_name = None
                    try:
                        if hasattr(box, 'cls') and box.cls is not None:
                            cls_idx = int(box.cls[0])
                            if hasattr(yolo_model, 'names'):
                                cls_name = yolo_model.names.get(cls_idx, str(cls_idx))
                            else:
                                cls_name = str(cls_idx)
                    except Exception:
                        pass
                    detections.append((x1, y1, x2, y2, conf, cls_name))
        return detections
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        return []


def check_defective(detection_boxes):
    """Check if any detection is defective."""
    for x1, y1, x2, y2, conf, cls_name in detection_boxes:
        if cls_name:
            cn = str(cls_name).strip().lower()
            if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                return True
    return False


def background_capture():
    """Capture thread: grab frames from the camera at full FPS.

    Runs independently of YOLO inference so that latest_frame stays fresh
    even while the detection thread is blocked on a slow inference call.
    """
    global latest_frame

    print("✓ Background capture thread started")

    while True:
        try:
            if camera is None:
                time.sleep(0.1)
                continue

            # capture_array() blocks until camera delivers a frame — natural pacing at camera FPS
            with camera_lock:
                frame = camera.capture_array()
            frame = normalize_frame(frame)
            frame = cv2.flip(frame, 0)

            with latest_frame_lock:
                latest_frame = frame

        except Exception as e:
            print(f"⚠ Capture error: {e}")
            time.sleep(0.1)


def background_detect():
    """Detection thread: run YOLO on the latest frame at a throttled rate.

    Reads from latest_frame (updated by the capture thread) so inference
    never blocks camera capture.  Results are cached for recv() to draw
    and published via ZMQ for servotest.
    """
    global last_detections, last_multi_detection

    last_log_time = 0
    prev_detection_count = -1

    print("✓ Background detection thread started")

    while True:
        try:
            # Grab the most recent frame from the capture thread
            with latest_frame_lock:
                frame = latest_frame.copy() if latest_frame is not None else None

            if frame is None or yolo_model is None:
                time.sleep(0.1)
                continue

            detection_boxes = run_detection(frame)
            is_defective = check_defective(detection_boxes)

            with detection_cache_lock:
                last_detections = detection_boxes
                last_multi_detection = len(detection_boxes) > 1

            # Publish via ZMQ for servotest
            if detection_publisher is not None:
                now = time.time()
                detection_msg = {
                    'detections': [
                        {
                            'x1': int(x1), 'y1': int(y1),
                            'x2': int(x2), 'y2': int(y2),
                            'confidence': float(conf),
                            'class': str(cls_name) if cls_name else 'mango'
                        }
                        for x1, y1, x2, y2, conf, cls_name in detection_boxes
                    ],
                    'is_defective': is_defective,
                    'multi_detection': len(detection_boxes) > 1,
                    'timestamp': now
                }
                try:
                    detection_publisher.send_json(detection_msg, flags=zmq.NOBLOCK)
                except zmq.Again:
                    pass

            # Throttled logging: only on change or every 5 seconds
            now = time.time()
            current_count = len(detection_boxes)
            if current_count != prev_detection_count or (now - last_log_time) >= 5.0:
                if detection_boxes:
                    print(f"🔍 YOLO detected {current_count} mango(es):")
                    for x1, y1, x2, y2, conf, cls_name in detection_boxes:
                        print(f"   - Class: {cls_name}, Confidence: {conf:.2f}")
                elif (now - last_log_time) >= 5.0:
                    print("⚪ No mangoes detected")
                print(f"   📊 is_defective={is_defective}")
                prev_detection_count = current_count
                last_log_time = now

            # Pace detection to avoid busy-looping (YOLO itself takes time,
            # but this ensures a minimum interval between runs)
            time.sleep(DETECTION_INTERVAL)

        except Exception as e:
            print(f"⚠ Detection error: {e}")
            time.sleep(0.5)


def draw_detection_boxes(frame, boxes):
    """Draw bounding boxes on frame.
    
    Color logic:
    - Green (0, 255, 0): Good mangoes (default or non-defective classes)
    - Red (0, 0, 255): Defective mangoes (class name contains 'defect', 'bad', etc.)
    """
    for x1, y1, x2, y2, conf, cls_name in boxes:
        # Determine color based on class name
        color = (0, 255, 0)  # Default: green for good mangoes
        if cls_name:
            cn = str(cls_name).strip().lower()
            # Mark as RED only if it's explicitly defective (ignore "not defective")
            if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                color = (0, 0, 255)  # Red for defective mangoes
        
        # Draw rectangle
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        
        # Draw label with class name and confidence
        label = f"{cls_name or 'Mango'} {conf:.2f}"
        cv2.putText(frame, label, (x1, max(20, y1 - 10)),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    
    return frame





class CameraTrack(VideoStreamTrack):
    """WebRTC video track that streams the latest captured frame.

    All heavy work (camera capture, YOLO inference, ZMQ publishing) now runs in
    background_capture_and_detect().  recv() only reads the cached frame and
    draws cached detection boxes — keeping the WebRTC encode path lightweight
    for smooth, low-latency video.
    """

    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps

    async def recv(self):
        """Return the latest frame with detection overlays for WebRTC."""
        pts, time_base = await self.next_timestamp()

        # Grab latest frame from background capture thread
        with latest_frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None

        if frame is None:
            frame = np.zeros((self.height, self.width, 3), np.uint8)

        # Draw cached detection boxes (updated by background thread)
        with detection_cache_lock:
            frame = draw_detection_boxes(frame, list(last_detections))

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
        return jsonify({'success': False, 'error': 'Server not ready - event loop not initialized'}), 503
    
    # Set the global event loop as current for this request thread
    asyncio.set_event_loop(loop)
    
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'success': False, 'error': 'Missing SDP offer or type'}), 400

    try:
        print(f"📡 Received WebRTC offer, processing...")
        offer_desc = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        pc = RTCPeerConnection()
        pcs.add(pc)
        print(f"✓ Created PeerConnection")

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            print('ICE state:', pc.iceConnectionState)
            if pc.iceConnectionState == 'failed':
                asyncio.run_coroutine_threadsafe(pc.close(), loop)

        camera_track = CameraTrack(width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS)
        pc.addTrack(camera_track)
        print(f"✓ Added camera track")

        async def run():
            await pc.setRemoteDescription(offer_desc)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return pc.localDescription

        # Schedule the async work on the global event loop and wait for result
        future = asyncio.run_coroutine_threadsafe(run(), loop)
        try:
            local_desc = future.result(timeout=15)
            print(f"✓ Generated WebRTC answer")
            return jsonify({'success': True, 'sdp': local_desc.sdp, 'type': local_desc.type})
        except asyncio.TimeoutError:
            print("❌ WebRTC answer generation timed out (>15s)")
            return jsonify({'success': False, 'error': 'Answer generation timeout'}), 500
    except Exception as e:
        error_msg = str(e) if str(e) else type(e).__name__
        print(f"❌ WebRTC offer error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/status')
def status():
    return jsonify({
        'stream_running': True,
        'resolution': f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
        'fps': CAMERA_FPS,
        'detection_mode': 'embedded_yolo'
    })


@app.route('/detection', methods=['GET'])
def detection():
    """Return current detection status including multi-mango alerts."""
    global last_detections, last_multi_detection
    with detection_cache_lock:
        return jsonify({
            'multi_detection': last_multi_detection,
            'detection_count': len(last_detections),
            'detections': [
                {'x1': int(x1), 'y1': int(y1), 'x2': int(x2), 'y2': int(y2), 'confidence': float(conf), 'class': str(cls_name)}
                for x1, y1, x2, y2, conf, cls_name in last_detections
            ]
        })


@app.route('/api/detections', methods=['GET'])
def get_detections():
    """Return latest detection results for servotest to consume."""
    global last_detections, last_multi_detection
    with detection_cache_lock:
        detections_formatted = []
        for x1, y1, x2, y2, conf, cls_name in last_detections:
            detections_formatted.append({
                'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                'confidence': conf,
                'class': cls_name
            })
        
        return jsonify({
            'detections': detections_formatted,
            'count': len(detections_formatted),
            'multi_detection': last_multi_detection,
            'timestamp': time.time()
        })


@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC camera stream running', 'endpoint': '/offer'})


if __name__ == '__main__':
    # Initialize ZMQ publisher for detection results
    try:
        zmq_context = zmq.Context()
        detection_publisher = zmq_context.socket(zmq.PUB)
        detection_publisher.setsockopt(zmq.SNDHWM, 1)  # Keep only latest message
        detection_publisher.bind(f"tcp://127.0.0.1:{DETECTION_PORT}")
        print(f"✓ ZMQ detection publisher started on tcp://127.0.0.1:{DETECTION_PORT}")
        time.sleep(0.5)  # Give subscribers time to connect
    except Exception as e:
        print(f"⚠ Failed to initialize ZMQ: {e}")
        detection_publisher = None

    print("Starting Mango Sorter WebRTC stream on port 8082...")
    print(f"Resolution: {CAMERA_WIDTH}x{CAMERA_HEIGHT} @ {CAMERA_FPS}fps")
    print(f"Detection interval: {DETECTION_INTERVAL}s (~{1/DETECTION_INTERVAL:.0f} Hz)")
    print("Access endpoint: http://0.0.0.0:8082/offer")

    load_yolo_model()  # Load YOLO model once at startup

    # Initialize camera and start background capture+detection thread
    try:
        init_camera()
    except Exception as e:
        print(f"⚠ Camera init failed: {e} — will stream black frames")

    cap_thread = threading.Thread(target=background_capture, daemon=True)
    cap_thread.start()

    det_thread = threading.Thread(target=background_detect, daemon=True)
    det_thread.start()

    setup_event_loop()
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
