#!/usr/bin/env python3
"""YOLO detection worker (runs in separate process).

Processes frames from webrtc_stream.py queue and returns bounding boxes.
This allows detection to run independently without blocking the camera stream.

Usage:
  python yolo_detector.py
  
(Automatically started by webrtc_stream.py if needed)
"""

import cv2
import multiprocessing
import time
import os
from ultralytics import YOLO

# Detection queue communication
detection_queue = multiprocessing.Queue()  # (frame_data, frame_id) ← from webrtc_stream
result_queue = multiprocessing.Queue()     # (boxes, frame_id) → to webrtc_stream

MODEL_PATH = os.environ.get(
    'YOLO_MODEL_PATH',
    os.path.abspath(os.path.join(os.path.dirname(__file__), 'final_weights.pt'))
)


def init_yolo_model():
    """Load YOLO model."""
    try:
        if not os.path.exists(MODEL_PATH):
            print(f"⚠ YOLO model not found at {MODEL_PATH}")
            return None
        yolo_model = YOLO(MODEL_PATH)
        print(f"✓ YOLO model loaded from {MODEL_PATH}")
        return yolo_model
    except Exception as e:
        print(f"⚠ Failed to load YOLO model: {e}")
        return None


def detect_frame(frame, yolo_model):
    """Run YOLO inference on frame and return bounding boxes.
    
    Returns:
        List of tuples: (x1, y1, x2, y2, confidence, class_name)
    """
    if frame is None or yolo_model is None:
        return []

    try:
        results = yolo_model(frame, verbose=False, conf=0.5)

        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            detections = []
            
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
                        cls_name = None
                    
                    detections.append((x1, y1, x2, y2, conf, cls_name))
            
            return detections
        
        return []
    except Exception as e:
        print(f"⚠ YOLO inference error: {e}")
        return []


def yolo_worker():
    """Main detection worker loop.
    
    Continuously reads frames from queue, runs YOLO inference,
    and puts results in the result queue.
    """
    print("🔍 YOLO detector process started")
    yolo_model = init_yolo_model()
    
    if yolo_model is None:
        print("❌ YOLO model failed to load, detector disabled")
        return
    
    frame_count = 0
    last_result_time = time.time()
    
    while True:
        try:
            # Wait for frame from webrtc_stream (timeout=1s to stay responsive)
            try:
                frame_bytes, frame_id = detection_queue.get(timeout=1)
            except:
                # No frame in queue, just continue
                continue
            
            # Decode frame
            try:
                nparr = np.frombuffer(frame_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception as e:
                print(f"⚠ Frame decode error: {e}")
                continue
            
            # Run detection
            start_time = time.time()
            detections = detect_frame(frame, yolo_model)
            detect_time = (time.time() - start_time) * 1000  # ms
            
            # Put results in queue (non-blocking)
            try:
                result_queue.put((detections, frame_id), block=False)
                
                frame_count += 1
                elapsed = time.time() - last_result_time
                if elapsed >= 10:  # Log every 10 seconds
                    print(f"🎯 YOLO: {frame_count} frames processed, {detect_time:.0f}ms per inference")
                    frame_count = 0
                    last_result_time = time.time()
            except:
                # Result queue full, drop this result
                pass
        
        except KeyboardInterrupt:
            print("🛑 YOLO detector stopping...")
            break
        except Exception as e:
            print(f"⚠ Detector error: {e}")
            time.sleep(0.1)


if __name__ == '__main__':
    import numpy as np
    
    # Run as standalone process
    try:
        yolo_worker()
    except KeyboardInterrupt:
        print("\n🛑 YOLO detector shutdown")
