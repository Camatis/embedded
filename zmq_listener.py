#!/usr/bin/env python3
"""
Listen for ZMQ detection messages to debug field names and structure
Run this on the Raspberry Pi while servotest.py is running and scanning mangoes
"""

import zmq
import json

try:
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 5000)  # 5 second timeout
    sub.connect('tcp://127.0.0.1:5555')
    sub.subscribe(b'')
    
    print("🎧 Listening for ZMQ messages on tcp://127.0.0.1:5555...")
    print("   Waiting up to 5 seconds between messages...")
    print("   (Trigger mango detection now!)\n")
    
    message_count = 0
    while True:
        try:
            msg = sub.recv_json()
            message_count += 1
            print(f"\n✓ Message #{message_count}:")
            print(f"  Full: {msg}")
            print(f"  Keys: {list(msg.keys())}")
            
            # Print individual field values for inspection
            for key, value in msg.items():
                print(f"    {key}: {value} (type: {type(value).__name__})")
            
        except zmq.Again:
            print("✗ Timeout - no message received in 5 seconds")
            if message_count == 0:
                print("   (No messages received yet - is webrtc_stream.py running?)")
        except json.JSONDecodeError as e:
            print(f"✗ JSON decode error: {e}")
        except KeyboardInterrupt:
            print("\n\n👋 Stopped listening.")
            break
        except Exception as e:
            print(f"✗ Error: {e}")
    
    sub.close()
    ctx.term()

except Exception as e:
    print(f"❌ Failed to connect: {e}")
    print("   Make sure servotest.py is running on this Pi")
    print("   Make sure webrtc_stream.py or yolo_detector.py is publishing messages")
