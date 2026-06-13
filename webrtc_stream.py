#!/usr/bin/env python3
"""WebRTC camera stream with shared YOLO detector (background-threaded).

Captures frames and streams them via WebRTC to browser.
YOLO detections run in a separate thread to keep WebRTC recv() lightweight.

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
        # OPTIMIZED: Using the highly efficient ONNX export weights
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.onnx'))
        if not os.path.exists(model_path):
            print(f"⚠ YOLO model not found at {model_path}")
            yolo_model = None
        else:
            # Explicitly state 'detect' task for smooth ONNX runtime mapping
            yolo_model = YOLO(model_path, task='detect')
            print(f"✓ YOLO ONNX model successfully loaded from {model_path}")
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        yolo_model = None

def init_camera():
    """Initialize Picamera2 with explicit RGB format for clean frame capture."""
    global camera
    if camera is not None:
        return camera

    try:
        camera = Picamera2()
        
        # Request RGB format explicitly to avoid format ambiguity
        config = camera.create_video_configuration(
            main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}
        )
        camera.configure(config)
        camera.start()
        print(f"✓ Picamera2 initialized: {CAMERA_WIDTH}x{CAMERA_HEIGHT} @ {CAMERA_FPS}fps (RGB888 format)")
        
        # Warm up camera with a few dummy captures
        for i in range(3):
            try:
                _ = camera.capture_array()
                time.sleep(0.05)
            except:
                pass
        print("✓ Camera warmup complete")
        
        return camera
    except Exception as e:
        print(f"⚠ Camera initialization failed: {e}")
        camera = None
        return None


def normalize_frame(frame):
    """Normalize frame to RGB format (what YOLO expects from Picamera2)."""
    if frame is None or frame.size == 0:
        return None
    
    try:
        if frame.ndim == 3 and frame.shape[2] == 3:
            if frame.dtype == np.uint8:
                return frame  
            return frame
        
        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_RGBA2RGB)
        
        if frame.ndim == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        
        if frame.ndim == 3 and frame.shape[2] == 1:
            return cv2.cvtColor(frame[:, :, 0], cv2.COLOR_GRAY2RGB)
        
        print(f"⚠ Unexpected frame format: shape={frame.shape}, dtype={frame.dtype}")
        return frame
        
    except Exception as e:
        print(f"⚠ Frame normalization failed: {e}")
        return None


def run_detection(frame):
    """Run YOLO detection on frame. Returns list of (x1,y1,x2,y2,conf,cls_name)."""
    if yolo_model is None:
        return []
    try:
        if not hasattr(run_detection, 'first_run'):
            print(f"   🎯 run_detection() called with frame: shape={frame.shape}, dtype={frame.dtype}")
            run_detection.first_run = True
        
        # OPTIMIZED PARAMS: Strict thresholds and native 480px frame scaling constraints
        results = yolo_model(frame, verbose=False, conf=0.4, iou=0.45, imgsz=480)
        
        raw_detections = []
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
                    raw_detections.append({"box": (x1, y1, x2, y2), "conf": conf, "class": cls_name})

        # --- DEDUPLICATION FILTER FOR CONFLICTING BOX CLASSIFICATIONS ---
        # If two distinct classes overlap on the same spot, prioritize the defect assignment
        final_detections = []
        skip_indices = set()

        for i, det1 in enumerate(raw_detections):
            if i in skip_indices:
                continue
            
            for j, det2 in enumerate(raw_detections):
                if i == j or j in skip_indices:
                    continue
                
                b1, b2 = det1["box"], det2["box"]
                center1 = ((b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2)
                center2 = ((b2[0] + b2[2]) / 2, (b2[1] + b2[3]) / 2)
                
                distance = np.sqrt((center1[0] - center2[0])**2 + (center1[1] - center2[1])**2)
                
                # Spatial checking distance boundary setup
                if distance < 35:
                    c1_name = str(det1["class"]).strip().lower()
                    c2_name = str(det2["class"]).strip().lower()
                    
                    # Defect safety prioritization overwrite check
                    is_det2_defective = 'not' not in c2_name and ('defect' in c2_name or 'bad' in c2_name or 'damaged' in c2_name or 'rotten' in c2_name)
                    
                    if is_det2_defective:
                        det1 = det2  # Override clean box data with the defective variant
                    
                    skip_indices.add(j)
            
            x1, y1, x2, y2 = det1["box"]
            final_detections.append((x1, y1, x2, y2, det1["conf"], det1["class"]))
        
        return final_detections
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        import traceback
        traceback.print_exc()
        return []


def check_defective(detection_boxes):
    """Check if any detection is defective."""
    for x1, y1, x2, y2, conf, cls_name in detection_boxes:
        if cls_name:
            cn = str(cls_name).strip().lower()
            if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                return True
    return False


def draw_detection_boxes(frame, boxes):
    """Draw bounding boxes on frame with explicit RGB structural colors."""
    for x1, y1, x2, y2, conf, cls_name in boxes:
        color = (0, 255, 0)  # Default: Green for clean runs
        if cls_name:
            cn = str(cls_name).strip().lower()
            if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                color = (255, 0, 0)  # Red for defects
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{cls_name or 'Mango'} {conf:.2f}"
        cv2.putText(frame, label, (x1, max(20, y1 - 10)),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame


def background_capture():
    """Capture thread: grab frames from the camera at full FPS."""
    global latest_frame, camera
    print("✓ Background capture thread started")
    
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 10

    while True:
        try:
            if camera is None:
                time.sleep(0.1)
                continue

            with camera_lock:
                frame = camera.capture_array()
            
            if frame is None:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"⚠ Camera returning None frames repeatedly, restarting...")
                    camera = None
                    init_camera()
                    consecutive_errors = 0
                time.sleep(0.1)
                continue
            
            if frame.size == 0:
                consecutive_errors += 1
                time.sleep(0.1)
                continue
            
            consecutive_errors = 0  
            frame = normalize_frame(frame)
            frame = np.ascontiguousarray(frame)
            
            h, w = frame.shape[:2]
            if h != CAMERA_HEIGHT or w != CAMERA_WIDTH:
                frame = cv2.resize(frame, (CAMERA_WIDTH, CAMERA_HEIGHT), interpolation=cv2.INTER_LINEAR)

            with latest_frame_lock:
                latest_frame = frame

        except Exception as e:
            print(f"⚠ Capture error: {e}")
            consecutive_errors += 1
            time.sleep(0.1)


def background_detect():
    """Detection thread: run YOLO on the latest frame at a throttled rate."""
    global last_detections, last_multi_detection

    last_log_time = 0
    prev_detection_count = -1
    first_frame_logged = False

    print("✓ Background detection thread started")

    while True:
        try:
            with latest_frame_lock:
                frame = latest_frame.copy() if latest_frame is not None else None

            if frame is None or yolo_model is None:
                time.sleep(0.1)
                continue
            
            if not first_frame_logged:
                print(f"   📸 First frame received: shape={frame.shape}, dtype={frame.dtype}")
                first_frame_logged = True

            detection_boxes = run_detection(frame)
            is_defective = check_defective(detection_boxes)

            with detection_cache_lock:
                last_detections = detection_boxes
                last_multi_detection = len(detection_boxes) > 1

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

            now = time.time()
            current_count = len(detection_boxes)
            if current_count != prev_detection_count or (now - last_log_time) >= 5.0:
                if detection_boxes:
                    print(f"🔍 YOLO detected {current_count} mango(es):")
                    for x1, y1, x2, y2, conf, cls_name in detection_boxes:
                        print(f"   - Class: {cls_name}, Confidence: {conf:.2f}, Box: ({x1},{y1})-({x2},{y2})")
                elif (now - last_log_time) >= 5.0:
                    print("⚪ No mangoes detected")
                print(f"   📊 is_defective={is_defective}")
                prev_detection_count = current_count
                last_log_time = now

            time.sleep(DETECTION_INTERVAL)

        except Exception as e:
            print(f"⚠ Detection error: {e}")
            time.sleep(0.5)


class CameraTrack(VideoStreamTrack):
    """WebRTC video track that streams the latest captured frame."""

    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps

    async def recv(self):
        """Return the latest frame with detection overlays for WebRTC."""
        pts, time_base = await self.next_timestamp()

        with latest_frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None

        if frame is None:
            frame = np.zeros((self.height, self.width, 3), np.uint8)

        with detection_cache_lock:
            boxes_count = len(last_detections)
            frame = draw_detection_boxes(frame, list(last_detections))

        try:
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
    
    asyncio.set_event_loop(loop)
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'success': False, 'error': 'Missing SDP offer or type'}), 400

    try:
        print(f"Pushing remote peer negotiation sequence profiles setup...")
        offer_desc = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            if pc.iceConnectionState == 'failed':
                asyncio.run_coroutine_threadsafe(pc.close(), loop)

        camera_track = CameraTrack(width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS)
        pc.addTrack(camera_track)

        async def run():
            await pc.setRemoteDescription(offer_desc)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return pc.localDescription

        future = asyncio.run_coroutine_threadsafe(run(), loop)
        try:
            local_desc = future.result(timeout=15)
            return jsonify({'success': True, 'sdp': local_desc.sdp, 'type': local_desc.type})
        except asyncio.TimeoutError:
            return jsonify({'success': False, 'error': 'Answer generation timeout'}), 500
    except Exception as e:
        error_msg = str(e) if str(e) else type(e).__name__
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
    try:
        zmq_context = zmq.Context()
        detection_publisher = zmq_context.socket(zmq.PUB)
        detection_publisher.setsockopt(zmq.SNDHWM, 1)  
        detection_publisher.bind(f"tcp://127.0.0.1:{DETECTION_PORT}")
        print(f"✓ ZMQ detection publisher started on tcp://127.0.0.1:{DETECTION_PORT}")
        time.sleep(0.5)  
    except Exception as e:
        print(f"⚠ Failed to initialize ZMQ: {e}")
        detection_publisher = None

    print("Starting Mango Sorter WebRTC stream on port 8082...")
    load_yolo_model()  

    try:
        init_camera()
    except Exception as e:
        print(f"⚠ Camera init failed: {e}")

    cap_thread = threading.Thread(target=background_capture, daemon=True)
    cap_thread.start()

    det_thread = threading.Thread(target=background_detect, daemon=True)
    det_thread.start()

    setup_event_loop()
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)