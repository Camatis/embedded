from flask import Flask, jsonify, request
from flask_cors import CORS
import RPi.GPIO as GPIO
import time
import board
import busio
import threading
import subprocess
import json
import os
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from ultralytics import YOLO

app = Flask(__name__)
CORS(app)

# ==========================================
# 1. SETUP HARDWARE & AI
# ==========================================
print("Initializing Hardware...")
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

print("Loading AI Brain (final weights.pt)...")
model = YOLO("final weights.pt")
DEFECTIVE_CLASS_NAME = "defective"

# --- IR Sensor Setup ---
IR_TRIGGER_PIN = 17
IR_MEDIUM_PIN = 27
IR_LARGE_PIN = 22

GPIO.setup(IR_TRIGGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_MEDIUM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_LARGE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- LIVE COUNTERS ---
count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0

# --- CONTROL STATE ---
CONTROL_FILE = "/tmp/mangosort_control.json"
previous_state = "STOPPED"

def get_system_state():
    """Reads the JSON file written by the Node.js backend"""
    try:
        if os.path.exists(CONTROL_FILE):
            with open(CONTROL_FILE, 'r') as f:
                data = json.load(f)
                return data.get("state", "STOPPED")
    except:
        pass
    return "STOPPED"

def autonomous_loop():
    global count_small, count_medium, count_large, count_defective, count_total, previous_state
    
    while True:
        current_state = get_system_state()
        
        # Reset counters if we just started a new batch
        if current_state == "RUNNING" and previous_state == "STOPPED":
            print("\n🚀 NEW BATCH STARTED! Resetting counters...")
            count_small = count_medium = count_large = count_defective = count_total = 0
            
        previous_state = current_state

        if current_state == "RUNNING":
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                count_total += 1
                
                # Snapshot & AI Check
                is_defective = False
                try:
                    subprocess.run(["rpicam-jpeg", "-o", "current_mango.jpg", "-t", "1", "--nopreview"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    results = model.predict(source="current_mango.jpg", conf=0.5, verbose=False)
                    for r in results:
                        for c in r.boxes.cls:
                            if model.names[int(c)].lower() == DEFECTIVE_CLASS_NAME.lower():
                                is_defective = True
                                break
                except Exception as e:
                    print(f"⚠️ Camera error: {e}")

                # Hardware Sizing & Routing
                # (Assuming your gate servos are defined here from previous setups)
                if is_defective:
                    print("🎯 DECISION: DEFECTIVE.")
                    count_defective += 1
                else:
                    print("🎯 DECISION: GOOD.")
                    # Size routing logic goes here...

                # Wait for clear
                while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    time.sleep(0.05) 
                time.sleep(0.2) 

        elif current_state == "PAUSED":
            time.sleep(0.5)
            
        elif current_state == "STOPPED":
            time.sleep(0.5)
            
        time.sleep(0.01)

@app.route('/sensors', methods=['GET'])
def get_sensors():
    try:
        trigger_detected = GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW
        medium_detected = GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW
        large_detected = GPIO.input(IR_LARGE_PIN) == GPIO.LOW
        
        return jsonify({
            'trigger': trigger_detected,
            'medium': medium_detected,
            'large': large_detected,
            'defective': False, 
            'detectedSize': None 
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        autonomous_thread = threading.Thread(target=autonomous_loop)
        autonomous_thread.daemon = True
        autonomous_thread.start()
        
        app.run(host='0.0.0.0', port=5001, debug=False)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        GPIO.cleanup()