#!/usr/bin/env python3
"""WebRTC camera stream with YOLO detector and status classification."""

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

# Global status vars
loop = None
camera = None
yolo_model = None
latest_frame = None
latest_frame_lock = threading.Lock()
camera_lock = threading.Lock()

# Threading & Detection state
last_detections = []
detection_cache_lock = threading.Lock()
frame_is_new = False
DETECTION_INTERVAL = 0.03
CAMERA_WIDTH, CAMERA_HEIGHT = 416, 312
CAMERA_FPS = 12

# ZMQ Setup
zmq_context = zmq.Context()
detection_publisher = zmq_context.socket(zmq.PUB)
detection_publisher.bind("tcp://127.0.0.1:5555")

def load_yolo_model():
    global yolo_model
    try:
        from ultralytics import YOLO
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.onnx'))
        yolo_model = YOLO(model_path, task='detect')
        print(f"✓ YOLO model loaded from {model_path}")
    except Exception as e:
        print(f"⚠ YOLO failed: {e}")

def get_color_and_status(cls_name):
    """Returns BGR color and boolean flags based on classification."""
    cn = str(cls_name).strip().lower()
    
    is_defective = any(k in cn for k in ['defect', 'bad', 'damaged', 'rotten'])
    is_carabao = 'carabao' in cn
    
    if is_defective:
        return (0, 0, 255), True, False # Red
    elif not is_carabao:
        return (0, 255, 255), False, True # Yellow
    else:
        return (0, 255, 0), False, False # Green

def run_detection(frame):
    """Run YOLO inference."""
    if yolo_model is None: return []
    results = yolo_model(frame, verbose=False, conf=0.4, iou=0.45, imgsz=480)
    final_dets = []
    if results and len(results) > 0:
        for box in results[0].boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            conf = float(box.conf[0])
            cls_idx = int(box.cls[0])
            cls_name = yolo_model.names.get(cls_idx, "unknown")
            final_dets.append((x1, y1, x2, y2, conf, cls_name))
    return final_dets

def background_detect():
    global last_detections
    while True:
        frame = None
        with latest_frame_lock:
            if frame_is_new:
                frame = latest_frame.copy()
        
        if frame is not None:
            detection_boxes = run_detection(frame)
            
            # Prepare metadata for ZMQ and internal state
            detections_data = []
            is_defective_any = False
            is_not_carabao_any = False
            
            for d in detection_boxes:
                _, _, _, _, _, cls_name = d
                _, is_def, is_not_car = get_color_and_status(cls_name)
                if is_def: is_defective_any = True
                if is_not_car: is_not_carabao_any = True
                detections_data.append(d)

            with detection_cache_lock:
                last_detections = detections_data
            
            # Publish to servotest.py
            msg = {
                'detections': detections_data,
                'is_defective': is_defective_any,
                'is_not_carabao': is_not_carabao_any,
                'timestamp': time.time()
            }
            try:
                detection_publisher.send_json(msg, flags=zmq.NOBLOCK)
            except: pass
            
        time.sleep(DETECTION_INTERVAL)

def draw_detection_boxes(frame, boxes):
    """Draws boxes based on classification colors."""
    for x1, y1, x2, y2, conf, cls_name in boxes:
        color, _, _ = get_color_and_status(cls_name)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"{cls_name} {conf:.2f}", (x1, y1 - 10), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame

class CameraTrack(VideoStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        with latest_frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), np.uint8)
        
        with detection_cache_lock:
            frame = draw_detection_boxes(frame, list(last_detections))
        
        video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

# --- Flask Routes ---
@app.route('/offer', methods=['POST'])
def offer():
    data = request.get_json()
    pc = RTCPeerConnection()
    pcs.add(pc)
    pc.addTrack(CameraTrack())
    
    async def negotiate():
        await pc.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'], type=data['type']))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    # Run in the main event loop
    loop = asyncio.get_event_loop()
    future = asyncio.run_coroutine_threadsafe(negotiate(), loop)
    local_desc = future.result()
    return jsonify({'sdp': local_desc.sdp, 'type': local_desc.type})

@app.route('/detection', methods=['GET'])
def detection():
    with detection_cache_lock:
        return jsonify({'detections': last_detections})

# --- Main Initialization ---
if __name__ == '__main__':
    # Initialize hardware
    camera = Picamera2()
    camera.configure(camera.create_video_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}))
    camera.start()
    
    load_yolo_model()
    
    # Start threads
    threading.Thread(target=lambda: (lambda c: [ (c.capture_array(), setattr(globals(), 'latest_frame', c.capture_array()), setattr(globals(), 'frame_is_new', True)) for _ in iter(int, 1) ])(camera), daemon=True).start()
    threading.Thread(target=background_detect, daemon=True).start()
    
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)