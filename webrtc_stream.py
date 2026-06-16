#!/usr/bin/env python3
"""WebRTC camera stream with shared YOLO detector (background-threaded).

Optimized with a Frame-Dropping Queue to achieve zero lag on Raspberry Pi.
Decoupled and scaled for stable frontend React Dashboard rendering.
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
    time.sleep(0.5)  
    print("✓ Global asyncio event loop started")

camera = None
camera_lock = threading.Lock()

# OPTIMIZED: Adjusted resolution metrics to stop web dashboard freezes and memory leaks
CAMERA_WIDTH = 416
CAMERA_HEIGHT = 312
CAMERA_FPS = 12

zmq_context = None
detection_publisher = None
DETECTION_PORT = 5555

last_detections = []
last_multi_detection = False
detection_cache_lock = threading.Lock()

latest_frame = None
latest_frame_lock = threading.Lock()

# Flag to let the capture thread know the detection thread is ready for a fresh frame
frame_is_new = False 

# Controlled throttling: Don't choke the CPU, give it breathing room between runs
DETECTION_INTERVAL = 0.03 

yolo_model = None

def load_yolo_model():
    """Load YOLO model globally at startup (only once)."""
    global yolo_model
    print("Loading YOLO model...")
    try:
        from ultralytics import YOLO
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.onnx'))
        if not os.path.exists(model_path):
            print(f"⚠ YOLO model not found at {model_path}")
            yolo_model = None
        else:
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
        config = camera.create_video_configuration(
            main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}
        )
        camera.configure(config)
        camera.start()
        print(f"✓ Picamera2 initialized: {CAMERA_WIDTH}x{CAMERA_HEIGHT} @ {CAMERA_FPS}fps")
        return camera
    except Exception as e:
        print(f"⚠ Camera initialization failed: {e}")
        camera = None
        return None

def run_detection(frame):
    """Run YOLO detection on frame. Returns list of (x1,y1,x2,y2,conf,cls_name)."""
    if yolo_model is None:
        return []
    try:
        # Imgsz stays 480 to match your final_weights.onnx natively without accuracy drop
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
                    except Exception:
                        pass
                    raw_detections.append({"box": (x1, y1, x2, y2), "conf": conf, "class": cls_name})

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
                
                if distance < 35:
                    c2_name = str(det2["class"]).strip().lower()
                    is_det2_defective = 'not' not in c2_name and ('defect' in c2_name or 'bad' in c2_name or 'damaged' in c2_name or 'rotten' in c2_name)
                    if is_det2_defective:
                        det1 = det2  
                    skip_indices.add(j)
            
            x1, y1, x2, y2 = det1["box"]
            final_detections.append((x1, y1, x2, y2, det1["conf"], det1["class"]))
        
        return final_detections
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        return []

def background_capture():
    """Capture thread: Grab raw frames safely without deadlocking the driver queue."""
    global latest_frame, camera, frame_is_new
    print("✓ Background capture thread started")
    
    while True:
        try:
            if camera is None:
                time.sleep(0.1)
                continue

            # Attempt to non-blockingly acquire camera resources to avoid multi-thread stalls
            if camera_lock.acquire(blocking=False):
                try:
                    frame = camera.capture_array()
                finally:
                    camera_lock.release()
            else:
                time.sleep(0.01)
                continue
            
            if frame is None or frame.size == 0:
                time.sleep(0.01)
                continue
            
            clean_frame = np.ascontiguousarray(frame).copy()
            
            with latest_frame_lock:
                latest_frame = clean_frame
                frame_is_new = True 

            time.sleep(1 / CAMERA_FPS) 
        except Exception as e:
            print(f"⚠ Capture error bypass: {e}")
            time.sleep(0.1)

def background_detect():
    """Detection thread: ONLY analyze fresh frames. Clears cache when empty."""
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
            
            if len(detection_boxes) == 0:
                with detection_cache_lock:
                    last_detections = []
                    last_multi_detection = False
                time.sleep(DETECTION_INTERVAL)
                continue

            is_defective = False
            for x1, y1, x2, y2, conf, cls_name in detection_boxes:
                if cls_name:
                    cn = str(cls_name).strip().lower()
                    if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                        is_defective = True

            with detection_cache_lock:
                last_detections = detection_boxes
                last_multi_detection = len(detection_boxes) > 1

            if detection_publisher is not None:
                now = time.time()
                detection_msg = {
                    'detections': [
                        {
                            'x1': int(x1), 'y1': int(y1), 'x2': int(x2), 'y2': int(y2),
                            'confidence': float(conf), 'class': str(cls_name) if cls_name else 'mango'
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

            time.sleep(DETECTION_INTERVAL)
        except Exception as e:
            print(f"⚠ Detection thread error: {e}")
            time.sleep(0.5)

class CameraTrack(VideoStreamTrack):
    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps

    async def recv(self):
        """Zero-lock isolated frame retrieval to eliminate web application crashes."""
        await asyncio.sleep(1 / self.fps)
        pts, time_base = await self.next_timestamp()
        
        with latest_frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None

        if frame is None:
            frame = np.zeros((self.height, self.width, 3), np.uint8)

        with detection_cache_lock:
            frame = draw_detection_boxes(frame, list(last_detections))

        try:
            frame = np.ascontiguousarray(frame)
            video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame
        except Exception as e:
            print(f"⚠ WebRTC Frame conversion error: {e}")
            black = np.zeros((self.height, self.width, 3), np.uint8)
            video_frame = VideoFrame.from_ndarray(black, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame

def draw_detection_boxes(frame, boxes):
    for x1, y1, x2, y2, conf, cls_name in boxes:
        color = (0, 255, 0)  
        if cls_name:
            cn = str(cls_name).strip().lower()
            if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                color = (255, 0, 0)  
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{cls_name or 'Mango'} {conf:.2f}"
        cv2.putText(frame, label, (x1, max(20, y1 - 10)),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame

@app.route('/offer', methods=['POST'])
def offer():
    global loop
    if loop is None:
        return jsonify({'success': False, 'error': 'Server not ready'}), 503
    asyncio.set_event_loop(loop)
    data = request.get_json()
    try:
        offer_desc = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            if pc.iceConnectionState == 'failed' or pc.iceConnectionState == 'closed':
                asyncio.run_coroutine_threadsafe(pc.close(), loop)
                pcs.discard(pc)

        camera_track = CameraTrack(width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS)
        pc.addTrack(camera_track)

        async def run():
            await pc.setRemoteDescription(offer_desc)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return pc.localDescription

        future = asyncio.run_coroutine_threadsafe(run(), loop)
        local_desc = future.result(timeout=15)
        return jsonify({'success': True, 'sdp': local_desc.sdp, 'type': local_desc.type})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/status')
def status():
    return jsonify({'stream_running': True, 'resolution': f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}", 'fps': CAMERA_FPS})

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
            detections_formatted.append({'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'confidence': conf, 'class': cls_name})
        return jsonify({'detections': detections_formatted, 'count': len(detections_formatted), 'multi_detection': last_multi_detection, 'timestamp': time.time()})

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