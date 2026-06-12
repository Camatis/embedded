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
import tensorflow as tf
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

# TFLite model (loaded once at startup, shared across all connections)
tflite_interpreter = None
tflite_input_details = None
tflite_output_details = None
tflite_model_path = None

def load_tflite_model():
    """Load TFLite model globally at startup (only once)."""
    global tflite_interpreter, tflite_input_details, tflite_output_details, tflite_model_path
    print("Loading TFLite YOLO model...")
    try:
        tflite_model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.tflite'))
        if not os.path.exists(tflite_model_path):
            print(f"⚠ TFLite model not found at {tflite_model_path}")
            tflite_interpreter = None
        else:
            tflite_interpreter = tf.lite.Interpreter(model_path=tflite_model_path)
            tflite_interpreter.allocate_tensors()
            tflite_input_details = tflite_interpreter.get_input_details()
            tflite_output_details = tflite_interpreter.get_output_details()
            print(f"✓ TFLite model loaded from {tflite_model_path}")
            print(f"  Input shape: {tflite_input_details[0]['shape']}")
            print(f"  Input dtype: {tflite_input_details[0]['dtype']}")
            if 'quantization' in tflite_input_details[0]:
                print(f"  Input quantization: {tflite_input_details[0]['quantization']}")
            print(f"  Output tensors: {len(tflite_output_details)}")
            for i, output in enumerate(tflite_output_details):
                print(f"    Output {i}: shape={output['shape']}, dtype={output['dtype']}")
    except Exception as e:
        print(f"⚠ Failed to load TFLite model: {e}")
        import traceback
        traceback.print_exc()
        tflite_interpreter = None

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
        """Capture frame, run YOLO detection, publish via ZMQ, and stream via WebRTC."""
        pts, time_base = await self.next_timestamp()

        if self.camera_available:
            try:
                with camera_lock:
                    frame = camera.capture_array()
                frame = normalize_frame(frame)
                frame = cv2.flip(frame, 0)
                
                # Run TFLite detection on every frame for reliable defect detection
                # Better real-time responsiveness for camera-based routing
                if tflite_interpreter is not None:
                    try:
                        detection_boxes = self._run_detection(frame)
                        is_defective = self._check_defective(detection_boxes)
                        
                        # DEBUG: Log detection details
                        if detection_boxes:
                            print(f"🔍 YOLO detected {len(detection_boxes)} mango(es):")
                            for x1, y1, x2, y2, conf, cls_name in detection_boxes:
                                print(f"   - Class: {cls_name}, Confidence: {conf:.2f}")
                        else:
                            print(f"⚪ No mangoes detected in frame")
                        
                        print(f"   📊 is_defective={is_defective}")
                        
                        # Update detection cache for /api/detections endpoint
                        with detection_cache_lock:
                            global last_detections, last_multi_detection
                            last_detections = detection_boxes
                            last_multi_detection = len(detection_boxes) > 1
                    except Exception as e:
                        print(f"⚠ Detection error: {e}")
                
                # ALWAYS publish latest detection state via ZMQ (every frame, not just every 2 frames)
                # This ensures servotest always gets fresh data
                if detection_publisher is not None:
                    with detection_cache_lock:
                        detection_msg = {
                            'detections': [
                                {
                                    'x1': int(x1), 'y1': int(y1), 
                                    'x2': int(x2), 'y2': int(y2),
                                    'confidence': float(conf),
                                    'class': str(cls_name) if cls_name else 'mango'
                                }
                                for x1, y1, x2, y2, conf, cls_name in last_detections
                            ],
                            'is_defective': self._check_defective(last_detections),
                            'multi_detection': last_multi_detection,
                            'timestamp': time.time()
                        }
                    try:
                        detection_publisher.send_json(detection_msg, flags=zmq.NOBLOCK)
                    except zmq.Again:
                        pass  # Queue full, skip this publish
                
                # Draw detection boxes on frame
                frame = draw_detection_boxes(frame, last_detections)
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
    
    def _run_detection(self, frame):
        """Run TFLite detection on frame."""
        if tflite_interpreter is None:
            return []
        
        try:
            # Convert BGR to RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Get input shape
            input_shape = tflite_input_details[0]['shape']
            img_h, img_w = int(input_shape[1]), int(input_shape[2])
            
            # Resize frame to model input size
            frame_resized = cv2.resize(frame_rgb, (img_w, img_h))
            
            # Normalize to float32 [0, 1]
            frame_normalized = frame_resized.astype(np.float32) / 255.0
            
            # Add batch dimension and ensure C-contiguous
            input_data = np.expand_dims(frame_normalized, axis=0)
            input_data = np.ascontiguousarray(input_data)
            
            # Set input tensor
            tflite_interpreter.set_tensor(tflite_input_details[0]['index'], input_data)
            
            # Run inference
            tflite_interpreter.invoke()
            
            # Get output
            output_data = tflite_interpreter.get_tensor(tflite_output_details[0]['index'])
            
            detections = []
            confidence_threshold = 0.5
            
            # Parse output - flexible to handle different shapes
            if output_data.size == 0:
                return []
            
            # Handle different output shapes
            if len(output_data.shape) == 3:
                # Shape: [batch, num_detections, values]
                for detection in output_data[0]:
                    if len(detection) < 5:
                        continue
                    
                    x, y, w, h, conf = detection[:5]
                    conf = float(conf)
                    
                    if conf < confidence_threshold:
                        continue
                    
                    # Convert center coords to corner coords
                    x1 = max(0, int((x - w / 2) * self.width))
                    y1 = max(0, int((y - h / 2) * self.height))
                    x2 = min(self.width, int((x + w / 2) * self.width))
                    y2 = min(self.height, int((y + h / 2) * self.height))
                    
                    # Get class name
                    cls_name = 'mango'
                    if len(detection) > 5:
                        class_probs = detection[5:]
                        cls_idx = int(np.argmax(class_probs))
                        cls_names = {0: 'good', 1: 'defective', 2: 'not defective'}
                        cls_name = cls_names.get(cls_idx, f'class_{cls_idx}')
                    
                    detections.append((x1, y1, x2, y2, conf, cls_name))
            elif len(output_data.shape) == 2:
                # Shape: [num_detections, values] - no batch dimension
                for detection in output_data:
                    if len(detection) < 5:
                        continue
                    
                    x, y, w, h, conf = detection[:5]
                    conf = float(conf)
                    
                    if conf < confidence_threshold:
                        continue
                    
                    x1 = max(0, int((x - w / 2) * self.width))
                    y1 = max(0, int((y - h / 2) * self.height))
                    x2 = min(self.width, int((x + w / 2) * self.width))
                    y2 = min(self.height, int((y + h / 2) * self.height))
                    
                    cls_name = 'mango'
                    if len(detection) > 5:
                        class_probs = detection[5:]
                        cls_idx = int(np.argmax(class_probs))
                        cls_names = {0: 'good', 1: 'defective', 2: 'not defective'}
                        cls_name = cls_names.get(cls_idx, f'class_{cls_idx}')
                    
                    detections.append((x1, y1, x2, y2, conf, cls_name))
            
            return detections
        except Exception as e:
            print(f"⚠ TFLite inference error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _check_defective(self, detection_boxes):
        """Check if any detection is defective."""
        for x1, y1, x2, y2, conf, cls_name in detection_boxes:
            if cls_name:
                cn = str(cls_name).strip().lower()
                # Check if explicitly defective (NOT "not defective")
                if 'not' not in cn and ('defect' in cn or 'bad' in cn or 'damaged' in cn or 'rotten' in cn):
                    return True
        return False


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
    print("Access endpoint: http://0.0.0.0:8082/offer")
    
    setup_event_loop()
    load_tflite_model()  # Load TFLite model once at startup
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)


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
    print("Access endpoint: http://0.0.0.0:8082/offer")
    
    setup_event_loop()
    load_tflite_model()  # Load TFLite model once at startup
    app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
