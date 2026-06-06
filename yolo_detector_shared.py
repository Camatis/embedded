#!/usr/bin/env python3
"""Shared YOLO detector service with TTL (time-to-live) for fresh detections.

Single result_queue shared by all clients (webrtc_stream, servotest).
Results include timestamp so clients can auto-clear stale detections.

Usage:
  detection_queue, result_queue = start_shared_detector()
  DETECTION_TTL_FRAMES = 5  # Clear if no new detection for 5 frames
"""

import multiprocessing
import time
import os
import cv2
import numpy as np
from ultralytics import YOLO


MODEL_PATH = os.environ.get(
    'YOLO_MODEL_PATH',
    os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.pt'))
)

# Global queues (shared across all clients)
detection_queue = None
result_queue = None
model = None

# TTL configuration - clients should use this
DETECTION_TTL_FRAMES = 5  # Clear boxes if no detection for 5+ frames


def run_yolo_detector():
    """Main YOLO detector worker - runs continuously.
    
    Reads frames from detection_queue, runs YOLO inference, puts results in shared result_queue.
    All results include timestamp for TTL-based clearing by clients.
    """
    global model
    
    print("🔍 Shared YOLO detector process started")
    
    # Load YOLO model
    try:
        if not os.path.exists(MODEL_PATH):
            print(f"⚠ YOLO model not found at {MODEL_PATH}")
            return
        model = YOLO(MODEL_PATH)
        print(f"✓ YOLO model loaded from {MODEL_PATH}")
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        return
    
    frame_count = 0
    last_log_time = time.time()
    
    while True:
        try:
            # Wait for frame (timeout to stay responsive)
            try:
                frame_bytes, frame_id, client_id = detection_queue.get(timeout=1)
            except:
                continue
            
            # Decode frame
            try:
                nparr = np.frombuffer(frame_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
            except Exception as e:
                print(f"⚠ Frame decode error: {e}")
                continue
            
            # Run detection
            try:
                results = model(frame, verbose=False, conf=0.6, imgsz=320)
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
                                    if hasattr(model, 'names'):
                                        cls_name = model.names.get(cls_idx, str(cls_idx))
                                    else:
                                        cls_name = str(cls_idx)
                            except Exception:
                                pass
                            detections.append((x1, y1, x2, y2, conf, cls_name))
                
                multi_detection = len(detections) > 1
                
                # Put results in SHARED queue with timestamp
                try:
                    result_queue.put(
                        (detections, frame_id, client_id, multi_detection, time.time()),
                        block=False
                    )
                    frame_count += 1
                    
                    elapsed = time.time() - last_log_time
                    if elapsed >= 2:  # Log every 2 seconds
                        print(f"🎯 Shared YOLO: {frame_count} frames processed, result queue size: {result_queue.qsize()}")
                        frame_count = 0
                        last_log_time = time.time()
                except:
                    # Queue full, drop result
                    pass
            except Exception as e:
                print(f"⚠ YOLO inference error: {e}")
        
        except KeyboardInterrupt:
            print("🛑 Shared YOLO detector stopping...")
            break
        except Exception as e:
            print(f"⚠ Detector error: {e}")
            time.sleep(0.1)


def start_shared_detector():
    """Initialize and start the shared YOLO detector process."""
    global detection_queue, result_queue
    
    # Create queues - larger size to handle multiple clients
    detection_queue = multiprocessing.Queue(maxsize=20)
    result_queue = multiprocessing.Queue(maxsize=20)
    
    # Start detector process
    detector_process = multiprocessing.Process(
        target=run_yolo_detector,
        daemon=True
    )
    detector_process.start()
    print("✓ Shared YOLO detector started")
    time.sleep(1)  # Give it time to load model
    
    return detection_queue, result_queue


if __name__ == '__main__':
    print("Starting shared YOLO detector service...")
    print("Note: This should be imported by servotest.py and webrtc_stream.py")
    print("Not meant to run standalone.")
    
    # For testing only:
    det_q, res_q = start_shared_detector()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
