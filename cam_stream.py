#!/usr/bin/env python3
"""WebRTC camera stream server for Raspberry Pi camera module.

Usage:
  pip install flask aiortc opencv-python
  python cam_stream.py

Then from browser:
  1) Create offer in JS via RTCPeerConnection
  2) POST { sdp, type } to http://<rpi-ip>:8081/offer
  3) Receive { sdp, type } answer and setRemoteDescription

The <video> element gets real-time frames from /dev/video0.
"""

import asyncio
import cv2
from flask import Flask, request, jsonify
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole
from av import VideoFrame

app = Flask(__name__)

pcs = set()

class CameraTrack(VideoStreamTrack):
    def __init__(self, cam_index=0, width=640, height=480, fps=15):
        super().__init__()
        self.cap = cv2.VideoCapture(cam_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.fps = fps

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            # return black frame if camera fails
            img = 255 * np.zeros((480, 640, 3), np.uint8)
            frame = img
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def stop(self):
        super().stop()
        try:
            self.cap.release()
        except Exception:
            pass

@app.route('/offer', methods=['POST'])
def offer():
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'error': 'Missing SDP offer'}), 400

    offer = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on('iceconnectionstatechange')
    def on_iceconnectionstatechange():
        print('ICE state:', pc.iceConnectionState)
        if pc.iceConnectionState == 'failed':
            asyncio.ensure_future(pc.close())

    camera = CameraTrack(cam_index=0, width=640, height=480, fps=15)
    pc.addTrack(camera)

    async def run():
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())

    return jsonify({'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type})

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'RPi WebRTC cam_stream running', 'endpoint': '/offer'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, debug=False)
