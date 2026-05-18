#!/usr/bin/env python3
"""WebRTC camera stream server for Raspberry Pi camera module.

Usage:
  pip install flask aiortc opencv-python
  python cam_stream.py

Then from browser:
  1) Create offer in JS via RTCPeerConnection
  2) POST { sdp, type } to http://<rpi-ip>:8081/offer
  3) Receive { sdp, type } answer and setRemoteDescription

The <video> element gets real-time frames from /dev/video0 or libcamera.
"""

import asyncio
import cv2
import numpy as np
import threading
import time
from flask import Flask, request, jsonify, Response
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole
from av import VideoFrame
from picamera2 import Picamera2
import os
import sys
from ultralytics import YOLO

app = Flask(__name__)
pcs = set()

# Single shared camera instance and lock to prevent concurrent Picamera2 opens
camera = None
camera_lock = threading.Lock()
yolo_model = None
model_lock = threading.Lock()
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 15
MODEL_PATH = os.environ.get(
    'YOLO_MODEL_PATH',
    os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.pt'))
)

last_detection_count = 0


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


def draw_bounding_boxes(frame, yolo_model):
    """Run YOLO inference and draw bounding boxes on the frame."""
    global last_detection_count
    if yolo_model is None:
        last_detection_count = 0
        return frame
    frame = normalize_frame(frame)
    if frame is None:
        return frame
    
    try:
        with model_lock:
            results = yolo_model(frame, verbose=False, conf=0.5)
        
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            current_count = int(len(boxes)) if boxes is not None else 0
            if current_count != last_detection_count:
                print(f"✓ YOLO detections: {current_count}")
                last_detection_count = current_count
            
            if boxes is not None and current_count > 0:
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    
                    # Draw bounding box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    
                    # Draw label with confidence
                    label = f"Mango {conf:.2f}"
                    cv2.putText(frame, label, (x1, max(20, y1 - 10)), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        else:
            last_detection_count = 0
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        last_detection_count = 0
    
    return frame


# MJPEG stream fallback for web UI (option A)
def mjpeg_generator(width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
    init_camera()
    init_yolo_model()
    frame_interval = 1.0 / fps if fps > 0 else 0.1
    try:
        while True:
            start = time.time()
            with camera_lock:
                frame = camera.capture_array()
            frame = normalize_frame(frame)
            
            # Draw bounding boxes
            frame = draw_bounding_boxes(frame, yolo_model)
            
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                frame_bytes = jpeg.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

            elapsed = time.time() - start
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    except GeneratorExit:
        return

@app.route('/mjpeg')
def mjpeg_stream():
    return Response(
        mjpeg_generator(width=640, height=480, fps=15),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/snapshot')
def snapshot():
    init_camera()
    init_yolo_model()
    with camera_lock:
        frame = camera.capture_array()
    frame = draw_bounding_boxes(frame, yolo_model)
    _, jpeg = cv2.imencode('.jpg', frame)
    return Response(jpeg.tobytes(), mimetype='image/jpeg')

@app.route('/detection-status')
def detection_status():
    return jsonify({
        'model_loaded': yolo_model is not None,
        'model_path': MODEL_PATH,
        'last_detection_count': last_detection_count
    })

class CameraTrack(VideoStreamTrack):
    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
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
                frame = cv2.flip(frame, 0)  # vertically flip if needed
                
                # Draw bounding boxes
                frame = draw_bounding_boxes(frame, yolo_model)
            except Exception as e:
                print(f"⚠ Camera read error: {e}")
                frame = 255 * np.zeros((self.height, self.width, 3), np.uint8)
        else:
            # Black frame fallback
            frame = 255 * np.zeros((self.height, self.width, 3), np.uint8)
        
        try:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame
        except Exception as e:
            print(f"⚠ Frame conversion error: {e}")
            # Return black frame on error
            black = np.zeros((self.height, self.width, 3), np.uint8)
            video_frame = VideoFrame.from_ndarray(black, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame

    def stop(self):
        super().stop()
        # Shared camera is closed at process shutdown, not per-track
        pass

@app.route('/offer', methods=['POST'])
def offer():
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'error': 'Missing SDP offer'}), 400

    try:
        offer = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on('iceconnectionstatechange')
        def on_iceconnectionstatechange():
            print('ICE state:', pc.iceConnectionState)
            if pc.iceConnectionState == 'failed':
                asyncio.ensure_future(pc.close())

        camera = CameraTrack(width=640, height=480, fps=15)
        pc.addTrack(camera)

        async def run():
            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run())

        return jsonify({'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type})
    except Exception as e:
        print(f"❌ WebRTC offer error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC cam_stream running', 'endpoint': '/offer'})

if __name__ == '__main__':
    print("Starting Mango Sorter Camera Stream on port 8081...")
    print("Access endpoint: http://0.0.0.0:8081/offer")
    print("MJPEG endpoint: http://0.0.0.0:8081/mjpeg")
    print("Snapshot endpoint: http://0.0.0.0:8081/snapshot")
    app.run(host='0.0.0.0', port=8081, debug=False, threaded=True)
