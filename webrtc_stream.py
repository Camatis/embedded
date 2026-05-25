#!/usr/bin/env python3
"""WebRTC camera stream with shared YOLO detector.

Captures frames and streams them via WebRTC to browser.
YOLO detections are handled by yolo_detector_shared.py (shared with servotest).

Usage:
  pip install flask aiortc opencv-python av picamera2 ultralytics
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
from flask import Flask, request, jsonify
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from picamera2 import Picamera2
import multiprocessing

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

CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
CAMERA_FPS = 30

# Shared YOLO detector queues (will be set by main)
detection_queue = None
result_queue = None
WEBRTC_CLIENT_ID = 'webrtc_stream'

# Cache latest detections
last_detection_boxes = []
last_frame_id = -1

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
    def __init__(self, width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=CAMERA_FPS):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0
        self.camera_available = False

        try:
            init_camera()
            self.camera_available = True
            print("✓ Camera initialized for WebRTC via shared Picamera2")
        except Exception as e:
            print(f"⚠ picamera2 error: {e}")
            self.camera_available = False

        if not self.camera_available:
            print("⚠ Camera not available - will stream black frames as fallback")

    async def recv(self):
        """Capture frame and stream via WebRTC (non-blocking)."""
        pts, time_base = await self.next_timestamp()

        if self.camera_available:
            try:
                with camera_lock:
                    frame = camera.capture_array()
                frame = normalize_frame(frame)
                frame = cv2.flip(frame, 0)
                
                # Send frame to shared YOLO detector every 4 frames
                frame_id = self.frame_count
                if (self.frame_count % 4) == 0 and detection_queue is not None:
                    try:
                        frame_bytes = cv2.imencode('.jpg', frame)[1].tobytes()
                        detection_queue.put((frame_bytes, frame_id, WEBRTC_CLIENT_ID), block=False)
                    except Exception as e:
                        # Queue full or error - skip, no blocking
                        pass
                
                # Check for results from shared YOLO detector (drain queue to get freshest)
                global last_detection_boxes, last_frame_id
                if result_queue is not None:
                    try:
                        while True:
                            detections, result_frame_id, client_id = result_queue.get(block=False)
                            # Only apply results from THIS client (ignore servotest/cam_stream2 results)
                            if client_id == WEBRTC_CLIENT_ID and result_frame_id > last_frame_id:
                                last_detection_boxes = detections
                                last_frame_id = result_frame_id
                    except:
                        # No new results in queue
                        pass
                
                # Draw latest detection boxes (non-blocking)
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
        'stream_running': True,
        'resolution': f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
        'fps': CAMERA_FPS,
        'detection_mode': 'separate_process'
    })


@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC camera stream running', 'endpoint': '/offer'})


if __name__ == '__main__':
    # Import and start shared YOLO detector
    try:
        from yolo_detector_shared import start_shared_detector
        print("Starting shared YOLO detector...")
        detection_queue, result_queue = start_shared_detector()
        print("✓ Shared YOLO detector initialized")
    except Exception as e:
        print(f"⚠ Failed to start shared YOLO detector: {e}")
        print("  webrtc_stream will run without detection")
    
    print("Starting Mango Sorter WebRTC stream on port 8082...")
    print("Access endpoint: http://0.0.0.0:8082/offer")
    
    setup_event_loop()
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
