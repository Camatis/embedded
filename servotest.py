import RPi.GPIO as GPIO
import time
import board
import busio
import threading 
import subprocess 
import multiprocessing
import cv2
import numpy as np
import signal
import sys
import requests
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ==========================================
# GRACEFUL SHUTDOWN SETUP
# ==========================================
def signal_handler(sig, frame):
    print(f'\n📋 {signal.Signals(sig).name} received, initiating graceful shutdown...')
    cleanup_hardware()
    sys.exit(0)

def cleanup_hardware():
    """Safely stop all hardware operations."""
    global sorting_active, sorting_paused, hopper_active, conveyor_pwm
    print('🛑 Stopping all hardware...')
    try:
        sorting_active = False
        sorting_paused = False
        hopper_active = False
        
        if 'conveyor_pwm' in globals():
            conveyor_pwm.stop()
            GPIO.output(RPWM, GPIO.LOW)
            GPIO.output(LPWM, GPIO.LOW)
            GPIO.output(R_EN, GPIO.LOW)
            GPIO.output(L_EN, GPIO.LOW)
            print('✅ Conveyor stopped')
        
        try:
            barrier_gate.angle = BARRIER_LOCKED
            small_gate.angle = GATE_CLOSED
            medium_gate.angle = GATE_CLOSED
            large_gate.angle = GATE_CLOSED
            print('✅ Servo gates closed')
        except Exception as e:
            print(f'⚠️ Error closing gates: {e}')
        
        try:
            if 'pca' in globals():
                pca.deinit()
            print('✅ PCA9685 deinitialized')
        except:
            pass
        
        GPIO.cleanup()
        print('✅ GPIO cleaned up')
    except Exception as e:
        print(f'⚠️ Error during cleanup: {e}')

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==========================================
# 1. SETUP HARDWARE & AI
# ==========================================
print("Initializing Hardware...")
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- IR Sensor Setup ---
IR_TRIGGER_PIN = 17 
IR_MEDIUM_PIN = 27   
IR_LARGE_PIN = 22    

GPIO.setup(IR_TRIGGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_MEDIUM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_LARGE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- DC Motor Setup (Conveyor) ---
RPWM = 12
LPWM = 13
R_EN = 16
L_EN = 26

GPIO.setup(RPWM, GPIO.OUT)
GPIO.setup(LPWM, GPIO.OUT)
GPIO.setup(R_EN, GPIO.OUT)
GPIO.setup(L_EN, GPIO.OUT)

conveyor_pwm = GPIO.PWM(RPWM, 100)
conveyor_pwm.start(0)

CONVEYOR_SPEED = 75
CONVEYOR_RAMP_STEP = 5
CONVEYOR_RAMP_DELAY = 0.05
current_conveyor_speed = 0
conveyor_lock = threading.Lock()

def set_conveyor_speed(target_speed):
    global current_conveyor_speed
    target_speed = max(0, min(100, target_speed))
    with conveyor_lock:
        if target_speed == current_conveyor_speed:
            return
        step = CONVEYOR_RAMP_STEP if target_speed > current_conveyor_speed else -CONVEYOR_RAMP_STEP
        for speed in range(current_conveyor_speed + step, target_speed + step, step):
            conveyor_pwm.ChangeDutyCycle(speed)
            current_conveyor_speed = speed
            time.sleep(CONVEYOR_RAMP_DELAY)
        conveyor_pwm.ChangeDutyCycle(target_speed)
        current_conveyor_speed = target_speed
        if target_speed == 0:
            time.sleep(0.12)

# --- Servo Setup (Gates) ---
i2c = busio.I2C(board.SCL, board.SDA)
pca = PCA9685(i2c)
pca.frequency = 50

barrier_gate = servo.Servo(pca.channels[0])
medium_gate = servo.Servo(pca.channels[2])
large_gate = servo.Servo(pca.channels[3])
small_gate = servo.Servo(pca.channels[6])   
rotating_gate = servo.ContinuousServo(pca.channels[12]) 

# Servo Angles
GATE_CLOSED = 70
GATE_OPEN = 0
BARRIER_LOCKED = 0
BARRIER_RELEASED = 90

print("Locking sorting gates to default positions...")
barrier_gate.angle = BARRIER_LOCKED
small_gate.angle = GATE_CLOSED
medium_gate.angle = GATE_CLOSED
large_gate.angle = GATE_CLOSED
time.sleep(1)

# ==========================================
# 2. TIMING VARIABLES 
# ==========================================
SIZE_SCAN_DURATION = 3.0   
TIME_TO_MEDIUM = 0.8  
TIME_TO_LARGE = 2.2   
SMALL_DROP_TIME = 2.0
MEDIUM_DROP_TIME = 2.3 
LARGE_DROP_TIME = 3.5  
STOPPER_DELAY = 1.5 

# ==========================================
# 3. LIVE COUNTERS & STATE
# ==========================================
count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0

sorting_active = False
sorting_paused = False
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}

gate_states = {
    "small": "closed",
    "medium": "closed",
    "large": "closed"
}

GPIO.output(R_EN, GPIO.HIGH)
GPIO.output(L_EN, GPIO.HIGH)
GPIO.output(LPWM, GPIO.LOW) 

hopper_active = True 

def pulse_hopper():
    while hopper_active:
        if not sorting_active:
            time.sleep(0.1)
            continue
        rotating_gate.throttle = -0.15  
        for _ in range(20):
            if not hopper_active: return
            time.sleep(0.1)
        rotating_gate.throttle = 0.0    
        for _ in range(30):
            if not hopper_active: return
            time.sleep(0.1)

hopper_thread = threading.Thread(target=pulse_hopper)
hopper_thread.daemon = True 
hopper_thread.start()

# ==========================================
# FLASK API ENDPOINTS
# ==========================================
@app.route('/api/hardware/status', methods=['GET'])
def get_hardware_status():
    return jsonify({
        'counts': {'small': count_small, 'medium': count_medium, 'large': count_large, 'defective': count_defective, 'total': count_total},
        'sensors': {
            'trigger': GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW,
            'medium': GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW,
            'large': GPIO.input(IR_LARGE_PIN) == GPIO.LOW
        },
        'state': 'active' if sorting_active else ('paused' if sorting_paused else 'idle'),
        'last_mango': last_mango
    })

@app.route('/api/hardware/control', methods=['POST'])
def hardware_control():
    global sorting_active, sorting_paused
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        sorting_active = True
        sorting_paused = False
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting started'})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action == 'resume':
        sorting_active = True
        sorting_paused = False
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting resumed'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting stopped'})
    
    return jsonify({'success': False, 'message': 'Invalid action'})

@app.route('/api/hardware/gate', methods=['POST'])
def control_gate_manual():
    global gate_states
    data = request.get_json()
    gate_name = data.get('gate')
    action = data.get('action')
    try:
        if action == 'open':
            if gate_name == 'SMALL':
                small_gate.angle = GATE_OPEN
                gate_states['small'] = 'open'
            elif gate_name == 'MEDIUM':
                medium_gate.angle = GATE_OPEN
                gate_states['medium'] = 'open'
            elif gate_name == 'LARGE':
                large_gate.angle = GATE_OPEN
                gate_states['large'] = 'open'
            return jsonify({'success': True, 'message': f'{gate_name} gate opened', 'gate_states': gate_states})
        elif action == 'close':
            if gate_name == 'SMALL':
                small_gate.angle = GATE_CLOSED
                gate_states['small'] = 'closed'
            elif gate_name == 'MEDIUM':
                medium_gate.angle = GATE_CLOSED
                gate_states['medium'] = 'closed'
            elif gate_name == 'LARGE':
                large_gate.angle = GATE_CLOSED
                gate_states['large'] = 'closed'
            return jsonify({'success': True, 'message': f'{gate_name} gate closed', 'gate_states': gate_states})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'Invalid gate or action'})

def operate_stopper():
    barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    barrier_gate.angle = BARRIER_LOCKED
    print("   [Stopper Locked]")

def route_small():
    global gate_states
    print("   [Small Gate Opened]")
    small_gate.angle = GATE_OPEN
    gate_states['small'] = 'open'
    time.sleep(SMALL_DROP_TIME) 
    small_gate.angle = GATE_CLOSED
    gate_states['small'] = 'closed'
    print("   [Small Gate Closed]")

def route_medium():
    global gate_states
    time.sleep(TIME_TO_MEDIUM)
    print("   [Medium Gate Opened]")
    medium_gate.angle = GATE_OPEN
    gate_states['medium'] = 'open'
    time.sleep(MEDIUM_DROP_TIME) 
    medium_gate.angle = GATE_CLOSED
    gate_states['medium'] = 'closed'
    print("   [Medium Gate Closed]")

def route_large():
    global gate_states
    time.sleep(TIME_TO_LARGE)
    print("   [Large Gate Opened]")
    large_gate.angle = GATE_OPEN
    gate_states['large'] = 'open'
    time.sleep(LARGE_DROP_TIME) 
    large_gate.angle = GATE_CLOSED
    gate_states['large'] = 'closed'
    print("   [Large Gate Closed]")

def scan_for_defect():
    is_defective = False
    try:
        headers = {'Connection': 'close'}
        response = requests.get('http://127.0.0.1:8082/detection', headers=headers, timeout=1.5)
        if response.status_code == 200:
            detection_data = response.json()
            detections_list = detection_data.get('detections', [])
            for d in detections_list:
                cls_name = str(d.get('class', '')).strip().lower()
                if 'not' not in cls_name and ('defect' in cls_name or 'bad' in cls_name or 'damaged' in cls_name or 'rotten' in cls_name):
                    is_defective = True
    except Exception as e:
        print(f"⚠️ API Fetch error: {e}")
    return is_defective

def flip_mango_for_second_scan():
    # SET TO 0.5 SECONDS
    print("▶️ RUNNING CONVEYOR FOR 0.5 SECONDS TO FLIP MANGO...")
    set_conveyor_speed(CONVEYOR_SPEED)
    time.sleep(0.5)
    print("🛑 STOPPING CONVEYOR...")
    set_conveyor_speed(0)

def autonomous_sorting_loop():
    global sorting_active, sorting_paused, count_small, count_medium, count_large, count_defective, count_total, last_mango
    
    while True:
        try:
            if not sorting_active:
                time.sleep(0.1)
                continue
                
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                print("🛑 STOPPING BELT FOR FIRST SIDE SCAN...")
                set_conveyor_speed(0)
                time.sleep(0.2)
                
                # --- SCAN SIDE 1 ---
                if scan_for_defect():
                    print("🎯 DECISION: Side 1 is DEFECTIVE. Instant Rejection.")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": "NONE", "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    threading.Thread(target=operate_stopper).start()
                    while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW: time.sleep(0.05)
                    time.sleep(0.2)
                    set_conveyor_speed(CONVEYOR_SPEED)
                    continue

                # --- FLIP STEP (0.5s) ---
                flip_mango_for_second_scan()
                time.sleep(0.2)
                
                # --- SCAN SIDE 2 ---
                if scan_for_defect():
                    print("🎯 DECISION: Side 2 is DEFECTIVE. Routing to reject bin.")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": "NONE", "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    threading.Thread(target=operate_stopper).start()
                    while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW: time.sleep(0.05)
                    time.sleep(0.2)
                    set_conveyor_speed(CONVEYOR_SPEED)
                    continue

                # --- BOTH SIDES GOOD: RUN SIZING SENSOR TRACK ---
                print("🎯 DECISION: Mango skin is healthy! Starting sizing run...")
                set_conveyor_speed(CONVEYOR_SPEED)
                
                detected_size = "SMALL" 
                end_time = time.time() + SIZE_SCAN_DURATION
                while time.time() < end_time:
                    if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                        detected_size = "LARGE"
                    elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                        detected_size = "MEDIUM"
                    time.sleep(0.01) 

                threading.Thread(target=operate_stopper).start() 
                
                count_total += 1
                if detected_size == "SMALL":
                    count_small += 1
                    threading.Thread(target=route_small).start()
                elif detected_size == "MEDIUM":
                    count_medium += 1
                    threading.Thread(target=route_medium).start()
                elif detected_size == "LARGE":
                    count_large += 1
                    threading.Thread(target=route_large).start()
                
                last_mango = {
                    "size": detected_size,
                    "health": "GOOD",
                    "timestamp": datetime.now().isoformat()
                }
                
                print("-" * 50)
                print(f"📊 LIVE COUNTS | Total: {count_total} | S: {count_small} | M: {count_medium} | L: {count_large} | Defective: {count_defective}")
                print("-" * 50)
                
                while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    time.sleep(0.05) 
                time.sleep(0.2)
                print("✅ Chamber clear. Ready for the next mango.")

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ ERROR in sorting loop: {e}")
            time.sleep(1)

# Service threads initialization
sorting_thread = threading.Thread(target=autonomous_sorting_loop)
sorting_thread.daemon = True
sorting_thread.start()

flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True))
flask_thread.daemon = True
flask_thread.start()

print("✅ MangoPain Hardware core listening on port 5000")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n🛑 Safety shutdown initiated...")
finally:
    cleanup_hardware()