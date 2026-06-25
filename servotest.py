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

# Hardware state tracking
sorting_active = False
sorting_paused = False
batch_state = 'idle'
conveyor_state = 'stopped'
count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0
last_mango = {
    'size': None,
    'health': None,
    'timestamp': None,
    'distance': None
}
gate_states = {
    'small': 'closed',
    'medium': 'closed',
    'large': 'closed'
}
multi_detection_flag = False
multi_detection_lock = threading.Lock()

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
        # Reset LEDs and Buzzer
        GPIO.output(GREEN_LED, GPIO.LOW)
        GPIO.output(RED_LED, GPIO.LOW)
        GPIO.output(BUZZER, GPIO.LOW)
        
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
# 1. SETUP HARDWARE
# ==========================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Sensors & GPIO ---
IR_TRIGGER_PIN = 17 
IR_MEDIUM_PIN = 27  
IR_LARGE_PIN = 22    

# --- LEDs & Buzzer ---
GREEN_LED = 5
RED_LED = 6
BUZZER = 24 # Buzzer updated to GPIO 24

GPIO.setup(IR_TRIGGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_MEDIUM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_LARGE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(GREEN_LED, GPIO.OUT)
GPIO.setup(RED_LED, GPIO.OUT)
GPIO.setup(BUZZER, GPIO.OUT)

def set_led(status):
    if status == "READY":
        GPIO.output(GREEN_LED, GPIO.HIGH); GPIO.output(RED_LED, GPIO.LOW)
    elif status == "BUSY":
        GPIO.output(GREEN_LED, GPIO.LOW); GPIO.output(RED_LED, GPIO.HIGH)
    else:
        GPIO.output(GREEN_LED, GPIO.LOW); GPIO.output(RED_LED, GPIO.LOW)

def trigger_buzzer(duration=0.5):
    GPIO.output(BUZZER, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(BUZZER, GPIO.LOW)

# --- DC Motor Setup ---
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

def set_conveyor_speed(target_speed, reverse=False):
    target_speed = max(0, min(100, target_speed))
    if reverse:
        GPIO.output(R_EN, GPIO.LOW)
        GPIO.output(L_EN, GPIO.HIGH)
        GPIO.output(LPWM, GPIO.HIGH) 
    else:
        GPIO.output(L_EN, GPIO.LOW)
        GPIO.output(R_EN, GPIO.HIGH)
    conveyor_pwm.ChangeDutyCycle(target_speed)

def reverse_conveyor_timed(seconds=1.0):
    print("🔄 REVERSING CONVEYOR...")
    set_conveyor_speed(CONVEYOR_SPEED, reverse=True)
    time.sleep(seconds)
    set_conveyor_speed(0)
    print("✅ Reverse operation complete.")

# --- Servo Setup ---
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

# --- Hopper ---
HOPPER_CHANNEL  = 12
HOPPER_REST     = 512
HOPPER_90_TICK  = 307
HOPPER_0_TICK   = 102
HOPPER_FULL_TRAVEL = 1.5

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
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
def scan_for_mango_data():
    time.sleep(4.0) 
    try:
        response = requests.get('http://127.0.0.1:8082/detection', timeout=2)
        if response.status_code == 200:
            data = response.json()
            dets = data.get('detections', [])
            is_defective = any('defect' in str(d.get('class','')).lower() for d in dets)
            is_not_carabao = any('not' in str(d.get('class','')).lower() or 'carabao' not in str(d.get('class','')).lower() for d in dets)
            return is_defective, is_not_carabao
    except: pass
    return False, False

def autonomous_sorting_loop():
    global sorting_active, sorting_paused, batch_state, conveyor_state, count_small, count_medium, count_large, count_defective, count_total, last_mango, multi_detection_flag
    while True:
        if not sorting_active:
            set_led("OFF")
            time.sleep(0.5)
            continue
        
        set_led("READY")

        if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
            set_led("BUSY")
            set_conveyor_speed(0)
            
            is_defective, is_not_carabao = scan_for_mango_data()
            
            if is_not_carabao:
                trigger_buzzer()
                print("🚨 Not Carabao Mango!")
                reverse_conveyor_timed(1.5)
                continue
                
            if is_defective:
                trigger_buzzer()
                count_defective += 1
                count_total += 1
                last_mango = {
                    'size': 'defective',
                    'health': 'DEFECTIVE',
                    'timestamp': datetime.now().isoformat(),
                    'distance': None
                }
                continue

            # [.. Size sorting logic ..]

        time.sleep(0.01)

@app.route('/api/hardware/status', methods=['GET'])
def get_hardware_status():
    return jsonify({
        'counts': {
            'small': count_small,
            'medium': count_medium,
            'large': count_large,
            'defective': count_defective,
            'total': count_total
        },
        'sensors': {
            'trigger': GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW,
            'medium': GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW,
            'large': GPIO.input(IR_LARGE_PIN) == GPIO.LOW
        },
        'state': 'running' if sorting_active else ('paused' if sorting_paused else 'idle'),
        'batch_state': batch_state,
        'conveyor_state': conveyor_state,
        'last_mango': last_mango
    })

@app.route('/api/hardware/control', methods=['POST'])
def hardware_control():
    global sorting_active, sorting_paused, batch_state, conveyor_state, count_small, count_medium, count_large, count_defective, count_total, last_mango
    data = request.get_json() or {}
    action = data.get('action', '').lower()

    if action == 'start':
        sorting_active = True
        sorting_paused = False
        batch_state = 'running'
        conveyor_state = 'running'
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting started'})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        batch_state = 'paused'
        conveyor_state = 'stopped'
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action in ('resume', 'continue'):
        sorting_active = True
        sorting_paused = False
        batch_state = 'running'
        conveyor_state = 'running'
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting resumed'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        batch_state = 'stopped'
        conveyor_state = 'stopped'
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting stopped'})

    return jsonify({'success': False, 'message': 'Invalid action'}), 400

@app.route('/api/hardware/gate', methods=['POST'])
def control_gate_manual():
    data = request.get_json() or {}
    gate_name = (data.get('gate') or '').lower()
    action = (data.get('action') or '').lower()

    if gate_name not in gate_states or action not in ('open', 'close'):
        return jsonify({'success': False, 'message': 'Invalid gate or action'}), 400

    gate_obj = {'small': small_gate, 'medium': medium_gate, 'large': large_gate}[gate_name]
    if action == 'open':
        gate_obj.angle = GATE_OPEN
        gate_states[gate_name] = 'open'
        return jsonify({'success': True, 'message': f'{gate_name} gate opened', 'gate_states': gate_states})
    else:
        gate_obj.angle = GATE_CLOSED
        gate_states[gate_name] = 'closed'
        return jsonify({'success': True, 'message': f'{gate_name} gate closed', 'gate_states': gate_states})

@app.route('/api/hardware/gate', methods=['GET'])
def get_gate_status():
    return jsonify({'gate_states': gate_states})

@app.route('/api/hardware/reset-counts', methods=['POST'])
def reset_counts():
    global count_small, count_medium, count_large, count_defective, count_total, last_mango
    count_small = 0
    count_medium = 0
    count_large = 0
    count_defective = 0
    count_total = 0
    last_mango = {'size': None, 'health': None, 'timestamp': None, 'distance': None}
    return jsonify({'success': True, 'message': 'All counts reset', 'counts': {'small': count_small, 'medium': count_medium, 'large': count_large, 'defective': count_defective, 'total': count_total}})

@app.route('/api/hardware/detection', methods=['GET'])
def get_detection_status():
    global multi_detection_flag
    with multi_detection_lock:
        flag = multi_detection_flag
        multi_detection_flag = False
    return jsonify({'multi_detection': flag})

@app.route('/api/hardware/sensors', methods=['GET'])
def get_sensor_diagnostics():
    return jsonify({
        'trigger': GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW,
        'medium': GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW,
        'large': GPIO.input(IR_LARGE_PIN) == GPIO.LOW,
        'defective': count_defective > 0,
        'detectedSize': last_mango.get('size'),
        'buzzerTriggered': False,
        'alertMessage': '',
        'twoMangoes': False
    })

# Service threads
sorting_thread = threading.Thread(target=autonomous_sorting_loop)
sorting_thread.daemon = True
sorting_thread.start()

flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True))
flask_thread.daemon = True
flask_thread.start()

try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    cleanup_hardware() 