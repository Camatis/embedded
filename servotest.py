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
import zmq
import json
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
    global sorting_active, sorting_paused, hopper_active, conveyor_pwm
    print('🛑 Stopping all hardware...')
    try:
        # Reset LEDs
        GPIO.output(GREEN_LED, GPIO.LOW)
        GPIO.output(RED_LED, GPIO.LOW)
        
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
            set_hopper(HOPPER_REST)
            barrier_gate.angle = BARRIER_LOCKED
            small_gate.angle = GATE_CLOSED
            medium_gate.angle = GATE_CLOSED
            large_gate.angle = GATE_CLOSED
            time.sleep(0.5)
            print('✅ Servo gates closed')
        except Exception as e:
            print(f'⚠️  Error closing gates: {e}')
        
        try:
            if 'pca' in globals():
                pca.deinit()
            print('✅ PCA9685 deinitialized')
        except:
            pass
        
        GPIO.cleanup()
        print('✅ GPIO cleaned up')
    except Exception as e:
        print(f'⚠️  Error during cleanup: {e}')

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==========================================
# 1. SETUP HARDWARE & AI
# ==========================================
print("Initializing Hardware...")
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Sensors & GPIO ---
IR_TRIGGER_PIN = 17 
IR_MEDIUM_PIN = 27  
IR_LARGE_PIN = 22    
ENTRANCE_IR = 23    # New Entrance Sensor

# --- LEDs & Buzzer ---
GREEN_LED = 5
RED_LED = 6
BUZZER = 16

GPIO.setup(IR_TRIGGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_MEDIUM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_LARGE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ENTRANCE_IR, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.setup(GREEN_LED, GPIO.OUT)
GPIO.setup(RED_LED, GPIO.OUT)
GPIO.setup(BUZZER, GPIO.OUT)

# Helper functions for new hardware
def set_led(status):
    if status == "READY":
        GPIO.output(GREEN_LED, GPIO.HIGH); GPIO.output(RED_LED, GPIO.LOW)
    elif status == "BUSY":
        GPIO.output(GREEN_LED, GPIO.LOW); GPIO.output(RED_LED, GPIO.HIGH)
    else: # OFF
        GPIO.output(GREEN_LED, GPIO.LOW); GPIO.output(RED_LED, GPIO.LOW)

def trigger_buzzer(duration=0.5):
    GPIO.output(BUZZER, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(BUZZER, GPIO.LOW)

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

def set_conveyor_speed(target_speed, reverse=False):
    global current_conveyor_speed, conveyor_state
    target_speed = max(0, min(100, target_speed))
    
    with conveyor_lock:
        # Motor direction control
        if reverse:
            GPIO.output(R_EN, GPIO.LOW)
            GPIO.output(L_EN, GPIO.HIGH)
            GPIO.output(LPWM, GPIO.HIGH) # Reverse logic
        else:
            GPIO.output(L_EN, GPIO.LOW)
            GPIO.output(R_EN, GPIO.HIGH)
            
        conveyor_pwm.ChangeDutyCycle(target_speed)
        current_conveyor_speed = target_speed
        conveyor_state = "running" if target_speed > 0 else "stopped"

def reverse_until_clear():
    print("🔄 REVERSING CONVEYOR...")
    set_conveyor_speed(CONVEYOR_SPEED, reverse=True)
    # Reverse until entrance sensor is no longer triggered (assuming LOW = object)
    while GPIO.input(ENTRANCE_IR) == GPIO.LOW:
        time.sleep(0.1)
    set_conveyor_speed(0)
    print("✅ Entrance clear.")

# --- Servo Setup (Gates) ---
i2c = busio.I2C(board.SCL, board.SDA)
pca = PCA9685(i2c)
pca.frequency = 50

barrier_gate = servo.Servo(pca.channels[0])
medium_gate = servo.Servo(pca.channels[2])
large_gate = servo.Servo(pca.channels[3])
small_gate = servo.Servo(pca.channels[6])

# Servo Angles
GATE_CLOSED = 70
GATE_OPEN = 0
BARRIER_LOCKED = 0
BARRIER_RELEASED = 90

# ==========================================
# HOPPER RAW PWM CONTROL
# ==========================================
HOPPER_CHANNEL  = 12
HOPPER_REST     = 512
HOPPER_90_TICK  = 307
HOPPER_0_TICK   = 102
HOPPER_FULL_TRAVEL = 1.5
HOPPER_HALF_TRAVEL = 1.0

def set_hopper(ticks):
    pca.channels[HOPPER_CHANNEL].duty_cycle = int(ticks * 65535 / 4096)

def initial_startup_drop():
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_0_TICK)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)

# Initialization
barrier_gate.angle = BARRIER_LOCKED
small_gate.angle = GATE_CLOSED
medium_gate.angle = GATE_CLOSED
large_gate.angle = GATE_CLOSED
initial_startup_drop()

# ==========================================
# 2. TIMING & COUNTERS
# ==========================================
CAMERA_SCAN_DELAY = 4.0    
SIZE_SCAN_DURATION = 3.0   
SMALL_DROP_TIME = 2.0      
MEDIUM_DROP_TIME = 2.3     
LARGE_DROP_TIME = 3.5      
STOPPER_DELAY = 1.5        

count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0

sorting_active = False
sorting_paused = False
batch_state = "idle"  
conveyor_state = "stopped"  
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}

gate_states = {"small": "closed", "medium": "closed", "large": "closed"}
multi_detection_flag = False
multi_detection_lock = threading.Lock()

# ==========================================
# FLASK API ENDPOINTS
# ==========================================
# [Existing API Endpoints - unchanged]
@app.route('/api/hardware/status', methods=['GET'])
def get_hardware_status():
    return jsonify({
        'counts': {'small': count_small, 'medium': count_medium, 'large': count_large, 'defective': count_defective, 'total': count_total},
        'state': 'active' if sorting_active else ('paused' if sorting_paused else 'idle'),
        'last_mango': last_mango
    })

@app.route('/api/hardware/control', methods=['POST'])
def hardware_control():
    global sorting_active, sorting_paused, batch_state
    data = request.get_json()
    action = data.get('action')
    if action == 'start':
        sorting_active = True
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        set_conveyor_speed(0)
        return jsonify({'success': True})
    elif action == 'continue':
        sorting_active = True
        sorting_paused = False
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True})
    elif action == 'stop':
        sorting_active = False
        set_conveyor_speed(0)
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/api/hardware/gate', methods=['GET', 'POST'])
def control_gate_manual():
    global gate_states
    if request.method == 'GET':
        return jsonify({'gate_states': gate_states})
    data = request.get_json() or {}
    gate_name = data.get('gate')
    action = data.get('action')
    # Add logic to set servos based on data.get('gate')
    return jsonify({'success': True})

# ==========================================
# 4. ROUTING SYSTEMS
# ==========================================
def operate_hopper_cycle():
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_90_TICK)
    time.sleep(HOPPER_HALF_TRAVEL)
    set_hopper(HOPPER_REST)

# [Existing execution functions (execute_small_delivery, etc.) - keep these as is]

# ==========================================
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
def scan_for_mango_data():
    """Returns (is_defective, is_not_carabao)"""
    time.sleep(CAMERA_SCAN_DELAY)
    try:
        response = requests.get('http://127.0.0.1:8082/detection', timeout=2)
        if response.status_code == 200:
            data = response.json()
            dets = data.get('detections', [])
            is_defective = any('defect' in str(d.get('class','')).lower() for d in dets)
            # Assuming 'not carabao' check is done via class name
            is_not_carabao = any('not' in str(d.get('class','')).lower() or 'carabao' not in str(d.get('class','')).lower() for d in dets)
            return is_defective, is_not_carabao
    except: pass
    return False, False # Default to good

def autonomous_sorting_loop():
    global sorting_active, count_total, count_defective, last_mango
    
    while True:
        if not sorting_active:
            set_led("OFF")
            time.sleep(0.5)
            continue
        
        # 1. Entrance Check
        if GPIO.input(ENTRANCE_IR) == GPIO.LOW:
            trigger_buzzer()
            print("🚨 Object detected at entry! Stopping.")
            set_conveyor_speed(0)
            while GPIO.input(ENTRANCE_IR) == GPIO.LOW:
                time.sleep(0.1)
            continue

        set_led("READY")

        # 2. Main Trigger
        if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
            set_led("BUSY")
            set_conveyor_speed(0)
            
            # Multi-mango check (Via ZMQ/Request)
            # If multi-detection == True, Reverse
            
            is_defective, is_not_carabao = scan_for_mango_data()
            
            if is_not_carabao:
                trigger_buzzer()
                print("🚨 Not Carabao Mango!")
                reverse_until_clear()
                continue
                
            if is_defective:
                execute_defective_delivery()
                continue

            # ... Proceed with size sorting logic ...

        time.sleep(0.01)

# Service threads
sorting_thread = threading.Thread(target=autonomous_sorting_loop)
sorting_thread.daemon = True
sorting_thread.start()

# Flask
flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True))
flask_thread.daemon = True
flask_thread.start()

try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    cleanup_hardware()