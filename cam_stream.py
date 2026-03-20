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
from flask import Flask, request, jsonify, Response
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole
from av import VideoFrame
from picamera2 import Picamera2
import os
import sys

app = Flask(__name__)
pcs = set()

# MJPEG stream fallback for web UI (option A)
def mjpeg_generator(width=640, height=480, fps=15):
    picam2 = Picamera2()
    config = picam2.create_video_configuration(main={"size": (width, height)})
    picam2.configure(config)
    picam2.start()
    
    try:
        while True:
            frame = picam2.capture_array()
            # Convert to JPEG bytes
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                frame_bytes = jpeg.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        picam2.stop()

@app.route('/mjpeg')
def mjpeg_stream():
    return Response(
        mjpeg_generator(width=640, height=480, fps=15),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/snapshot')
def snapshot():
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": (640, 480)})
    picam2.configure(config)
    picam2.start()
    frame = picam2.capture_array()
    picam2.stop()
    _, jpeg = cv2.imencode('.jpg', frame)
    return Response(jpeg.tobytes(), mimetype='image/jpeg')

class CameraTrack(VideoStreamTrack):
    def __init__(self, width=640, height=480, fps=15):
        super().__init__()
        self.picam2 = None
        self.width = width
        self.height = height
        self.fps = fps
        self.camera_available = False
        
        # Try to initialize camera with picamera2
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_video_configuration(main={"size": (width, height)})
            self.picam2.configure(config)
            self.picam2.start()
            # Test capture
            _ = self.picam2.capture_array()
            self.camera_available = True
            print("✓ Camera initialized via picamera2")
        except Exception as e:
            print(f"⚠ picamera2 error: {e}")
            self.camera_available = False
        
        if not self.camera_available:
            print("⚠ Camera not available - will stream black frames as fallback")
            print("⚠ Camera not available - will stream black frames as fallback")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        
        if self.camera_available and self.picam2:
            try:
                frame = self.picam2.capture_array()
                frame = cv2.flip(frame, 0)  # vertically flip if needed
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
        try:
            if self.picam2:
                self.picam2.stop()
        except Exception as e:
            print(f"⚠ Error closing camera: {e}")

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
    print("Access endpoint: http://127.0.0.1:8081/offer")
    print("MJPEG endpoint: http://127.0.0.1:8081/mjpeg")
    print("Snapshot endpoint: http://127.0.0.1:8081/snapshot")
    app.run(host='127.0.0.1', port=8081, debug=False, threaded=True)
