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
import logging
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

# Mute Flask server default terminal logging spam
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)

# ==========================================
# PIN & STATE VARIABLES CONFIGURATION
# ==========================================
# --- IR Sensor Pins ---
IR_TRIGGER_PIN = 17 
IR_MEDIUM_PIN = 27   
IR_LARGE_PIN = 22    

# --- Visual & Audio Indicator Pins ---
PIN_GREEN_LED = 5
PIN_RED_LED = 6
PIN_BUZZER = 16

# --- DC Motor Driver Pins (Conveyor H-Bridge) ---
RPWM = 12
LPWM = 13
R_EN = 16
L_EN = 26

# Conveyor Tracking Variables
CONVEYOR_SPEED = 75
CONVEYOR_RAMP_STEP = 5
CONVEYOR_RAMP_DELAY = 0.05
current_conveyor_speed = 0
conveyor_lock = threading.Lock()

# Servo Angular Calibration Constants
GATE_CLOSED = 70
GATE_OPEN = 0
BARRIER_LOCKED = 0
BARRIER_RELEASED = 90

# Mechatronic Delay Windows 
SIZE_SCAN_DURATION = 3.0   
TIME_TO_MEDIUM = 0.8  
TIME_TO_LARGE = 2.2   
SMALL_DROP_TIME = 2.0
MEDIUM_DROP_TIME = 2.3 
LARGE_DROP_TIME = 3.5  
STOPPER_DELAY = 1.5 

# Operational System Runtime State Flags
count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0

sorting_active = False
sorting_paused = False
ui_popup_active = False  
hopper_active = True 
ai_detection_state = {"multi_detection": False, "variety_error": False, "organic_error": False}
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}

gate_states = {
    "small": "closed",
    "medium": "closed",
    "large": "closed"
}

# Global hardware objects declared as None to be safely built inside the initialization scope
i2c = None
pca = None
barrier_gate = None
medium_gate = None
large_gate = None
small_gate = None
rotating_gate = None
conveyor_pwm = None

# ==========================================
# HARDWARE SETUP INTERFACE DEFINITION
# ==========================================
def setup_hardware():
    """Initializes all GPIO parameters, I2C buses, and sets defaults."""
    global i2c, pca, barrier_gate, medium_gate, large_gate, small_gate, rotating_gate, conveyor_pwm
    
    print("Initializing Hardware Modules...")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # Configure IR Sensors
    GPIO.setup(IR_TRIGGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(IR_MEDIUM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(IR_LARGE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Configure Telemetry Indicators
    GPIO.setup(PIN_GREEN_LED, GPIO.OUT)
    GPIO.setup(PIN_RED_LED, GPIO.OUT)
    GPIO.setup(PIN_BUZZER, GPIO.OUT)

    # Configure Conveyor Output Channels
    GPIO.setup(RPWM, GPIO.OUT)
    GPIO.setup(LPWM, GPIO.OUT)
    GPIO.setup(R_EN, GPIO.OUT)
    GPIO.setup(L_EN, GPIO.OUT)

    # Enable H-Bridge Controllers
    GPIO.output(R_EN, GPIO.HIGH)
    GPIO.output(L_EN, GPIO.HIGH)
    GPIO.output(LPWM, GPIO.LOW)

    conveyor_pwm = GPIO.PWM(RPWM, 100)
    conveyor_pwm.start(0)

    # Bind I2C & PCA9685 Controllers
    i2c = busio.I2C(board.SCL, board.SDA)
    pca = PCA9685(i2c)
    pca.frequency = 50

    barrier_gate = servo.Servo(pca.channels[0])
    medium_gate = servo.Servo(pca.channels[2])
    large_gate = servo.Servo(pca.channels[3])
    small_gate = servo.Servo(pca.channels[6])    
    rotating_gate = servo.ContinuousServo(pca.channels[12]) 

    print("Locking sorting gates to default positions...")
    barrier_gate.angle = BARRIER_LOCKED
    small_gate.angle = GATE_CLOSED
    medium_gate.angle = GATE_CLOSED
    large_gate.angle = GATE_CLOSED
    
    set_machine_ready_leds()
    print("✅ Hardware core initialization complete.")

# ==========================================
# GRACEFUL SHUTDOWN INTERFACE
# ==========================================
def signal_handler(sig, frame):
    print(f'\n📋 {signal.Signals(sig).name} received, initiating graceful shutdown...')
    cleanup_hardware()
    sys.exit(0)

def cleanup_hardware():
    """Safely terminates ongoing hardware routines to avoid short-circuits."""
    global sorting_active, sorting_paused, hopper_active, conveyor_pwm
    print('🛑 Stopping all hardware...')
    try:
        sorting_active = False
        sorting_paused = False
        hopper_active = False
        
        if conveyor_pwm is not None:
            conveyor_pwm.stop()
            GPIO.output(RPWM, GPIO.LOW)
            GPIO.output(LPWM, GPIO.LOW)
            GPIO.output(R_EN, GPIO.LOW)
            GPIO.output(L_EN, GPIO.LOW)
            print('✅ Conveyor stopped')
        
        try:
            if barrier_gate is not None:
                barrier_gate.angle = BARRIER_LOCKED
                small_gate.angle = GATE_CLOSED
                medium_gate.angle = GATE_CLOSED
                large_gate.angle = GATE_CLOSED
                print('✅ Servo gates closed')
        except Exception as e:
            print(f'⚠️ Error closing gates: {e}')
        
        try:
            if pca is not None:
                pca.deinit()
            print('✅ PCA9685 deinitialized')
        except:
            pass
        
        GPIO.output(PIN_GREEN_LED, GPIO.LOW)
        GPIO.output(PIN_RED_LED, GPIO.LOW)
        GPIO.output(PIN_BUZZER, GPIO.LOW)
        
        GPIO.cleanup()
        print('✅ GPIO cleaned up')
    except Exception as e:
        print(f'⚠️ Error during cleanup: {e}')

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==========================================
# MECHATRONIC ACTUATION UTILITIES
# ==========================================
def set_conveyor_speed(target_speed, direction="FORWARD"):
    global current_conveyor_speed
    target_speed = max(0, min(100, target_speed))
    
    if direction == "REVERSE":
        GPIO.output(RPWM, GPIO.LOW)
        GPIO.output(LPWM, GPIO.HIGH)
    else: 
        GPIO.output(RPWM, GPIO.HIGH)
        GPIO.output(LPWM, GPIO.LOW)

    with conveyor_lock:
        if target_speed == current_conveyor_speed:
            return
        
        step = CONVEYOR_RAMP_STEP if target_speed > current_conveyor_speed else -CONVEYOR_RAMP_STEP
        for speed in range(current_conveyor_speed + step, target_speed + step, step):
            if conveyor_pwm is not None:
                conveyor_pwm.ChangeDutyCycle(speed)
            current_conveyor_speed = speed
            time.sleep(CONVEYOR_RAMP_DELAY)
            
        if conveyor_pwm is not None:
            conveyor_pwm.ChangeDutyCycle(target_speed)
        current_conveyor_speed = target_speed
        if target_speed == 0:
            time.sleep(0.12)

def set_machine_ready_leds():
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.LOW)

def set_machine_active_leds():
    GPIO.output(PIN_GREEN_LED, GPIO.LOW)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)

def pulse_hopper():
    while hopper_active:
        if not sorting_active:
            time.sleep(0.1)
            continue
        if rotating_gate is not None:
            rotating_gate.throttle = -0.15  
        for _ in range(20):
            if not hopper_active: return
            time.sleep(0.1)
        if rotating_gate is not None:
            rotating_gate.throttle = 0.0    
        for _ in range(30):
            if not hopper_active: return
            time.sleep(0.1)

# ==========================================
# FLASK INTERFACE CORE API ROUTING
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

@app.route('/api/hardware/detection', methods=['GET'])
def get_hardware_detection_state():
    return jsonify(ai_detection_state)

@app.route('/api/hardware/gate', methods=['GET', 'POST'])
def control_gate_manual():
    global gate_states
    if request.method == 'GET':
        return jsonify({
            'gate_states': gate_states,
            'gate_angles': {
                'small': small_gate.angle if small_gate and hasattr(small_gate, 'angle') else None,
                'medium': medium_gate.angle if medium_gate and hasattr(medium_gate, 'angle') else None,
                'large': large_gate.angle if large_gate and hasattr(large_gate, 'angle') else None
            }
        })
        
    data = request.get_json() or {}
    gate_name = data.get('gate')
    action = data.get('action')
    try:
        if action == 'open':
            if gate_name == 'SMALL' and small_gate:
                small_gate.angle = GATE_OPEN
                gate_states['small'] = 'open'
            elif gate_name == 'MEDIUM' and medium_gate:
                medium_gate.angle = GATE_OPEN
                gate_states['medium'] = 'open'
            elif gate_name == 'LARGE' and large_gate:
                large_gate.angle = GATE_OPEN
                gate_states['large'] = 'open'
            return jsonify({'success': True, 'message': f'{gate_name} gate opened', 'gate_states': gate_states})
        elif action == 'close':
            if gate_name == 'SMALL' and small_gate:
                small_gate.angle = GATE_CLOSED
                gate_states['small'] = 'closed'
            elif gate_name == 'MEDIUM' and medium_gate:
                medium_gate.angle = GATE_CLOSED
                gate_states['medium'] = 'closed'
            elif gate_name == 'LARGE' and large_gate:
                large_gate.angle = GATE_CLOSED
                gate_states['large'] = 'closed'
            return jsonify({'success': True, 'message': f'{gate_name} gate closed', 'gate_states': gate_states})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': False, 'message': 'Invalid gate or action'}), 400

@app.route('/api/hardware/control', methods=['POST'])
def hardware_control():
    global sorting_active, sorting_paused, ui_popup_active, ai_detection_state
    data = request.get_json() or {}
    action = data.get('action')
    
    if action == 'start':
        sorting_active = True
        sorting_paused = False
        set_machine_active_leds()
        set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
        return jsonify({'success': True, 'message': 'Sorting started'})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action == 'resume' or action == 'continue':
        ui_popup_active = False
        ai_detection_state = {"multi_detection": False, "variety_error": False, "organic_error": False}
        sorting_active = True
        sorting_paused = False
        set_machine_active_leds()
        set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
        return jsonify({'success': True, 'message': 'Sorting resumed'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        set_conveyor_speed(0)
        reset_servos_to_default_internal()
        set_machine_ready_leds()
        return jsonify({'success': True, 'message': 'Sorting stopped'})
    
    return jsonify({'success': False, 'message': 'Invalid action'})

def reset_servos_to_default_internal():
    global gate_states
    if barrier_gate: barrier_gate.angle = BARRIER_LOCKED
    if small_gate: small_gate.angle = GATE_CLOSED
    if medium_gate: medium_gate.angle = GATE_CLOSED
    if large_gate: large_gate.angle = GATE_CLOSED
    gate_states = {"small": "closed", "medium": "closed", "large": "closed"}

def operate_stopper():
    if barrier_gate: barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    if barrier_gate: barrier_gate.angle = BARRIER_LOCKED
    print("   [Stopper Locked]")

def route_small():
    global gate_states
    print("   [Small Gate Opened]")
    if small_gate: small_gate.angle = GATE_OPEN
    gate_states['small'] = 'open'
    time.sleep(SMALL_DROP_TIME) 
    if small_gate: small_gate.angle = GATE_CLOSED
    gate_states['small'] = 'closed'
    print("   [Small Gate Closed]")

def route_medium():
    global gate_states
    time.sleep(TIME_TO_MEDIUM)
    print("   [Medium Gate Opened]")
    if medium_gate: medium_gate.angle = GATE_OPEN
    gate_states['medium'] = 'open'
    time.sleep(MEDIUM_DROP_TIME) 
    if medium_gate: medium_gate.angle = GATE_CLOSED
    gate_states['medium'] = 'closed'
    print("   [Medium Gate Closed]")

def route_large():
    global gate_states
    time.sleep(TIME_TO_LARGE)
    print("   [Large Gate Opened]")
    if large_gate: large_gate.angle = GATE_OPEN
    gate_states['large'] = 'open'
    time.sleep(LARGE_DROP_TIME) 
    if large_gate: large_gate.angle = GATE_CLOSED
    gate_states['large'] = 'closed'
    print("   [Large Gate Closed]")

# ==========================================
# EXCLUSIONARY PIPELINE HANDLING ROUTINES
# ==========================================
def execute_mechanical_rejection(alert_type):
    global sorting_active, sorting_paused, ui_popup_active
    sorting_active = False
    sorting_paused = True
    ui_popup_active = True
    
    print(f"🚨 HARDWARE CRITICAL ALERT: Triggering Rejection Mechanism via {alert_type}")
    set_conveyor_speed(0)
    reset_servos_to_default_internal()
    
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(1.2)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    print("◀️ REVERSING CONVEYOR ACTUATORS TO EJECT FROM ENTRY PORT...")
    set_conveyor_speed(CONVEYOR_SPEED, "REVERSE")
    time.sleep(2.5)
    set_conveyor_speed(0)

def scan_vision_pipeline():
    is_defective = False
    multi_mango = False
    variety_valid = True
    organic_valid = True
    
    try:
        headers = {'Connection': 'close'}
        response = requests.get('http://127.0.0.1:8082/detection', headers=headers, timeout=1.5)
        if response.status_code == 200:
            detection_data = response.json()
            
            if detection_data.get('multi_detection') is True:
                multi_mango = True
                return is_defective, multi_mango, variety_valid, organic_valid
                
            detections_list = detection_data.get('detections', [])
            
            if not detections_list or len(detections_list) == 0:
                organic_valid = False
                return is_defective, multi_mango, variety_valid, organic_valid

            for d in detections_list:
                cls_name = str(d.get('class', '')).strip().lower()
                
                if 'not carabao' in cls_name:
                    variety_valid = False
                    break
                
                if 'not' not in cls_name and ('defect' in cls_name or 'bad' in cls_name or 'damaged' in cls_name or 'rotten' in cls_name):
                    is_defective = True
                    
    except Exception as e:
        print(f"⚠️ Vision Pipeline Link Failure: {e}")
        
    return is_defective, multi_mango, variety_valid, organic_valid

def flip_mango_for_second_scan():
    print("▶️ RUNNING CONVEYOR FOR 0.5 SECONDS TO FLIP MANGO...")
    set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
    time.sleep(0.5)
    print("🛑 STOPPING CONVEYOR...")
    set_conveyor_speed(0)

# ==========================================
# CORE PROCESSING PIPELINE ITERATOR
# ==========================================
def autonomous_sorting_loop():
    global sorting_active, sorting_paused, count_small, count_medium, count_large, count_defective, count_total, last_mango, ai_detection_state
    
    while True:
        try:
            if not sorting_active:
                time.sleep(0.1)
                continue
                
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                set_machine_active_leds()
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                print("🛑 STOPPING BELT FOR FIRST SIDE SCAN...")
                set_conveyor_speed(0)
                time.sleep(0.2)
                
                # --- PROCESS SIDE 1 MATRIX ---
                is_defective, multi_mango, variety_valid, organic_valid = scan_vision_pipeline()
                
                if multi_mango:
                    ai_detection_state["multi_detection"] = True
                    execute_mechanical_rejection("MULTI_MANGO_TRAP")
                    continue
                
                if not organic_valid:
                    ai_detection_state["organic_error"] = True
                    execute_mechanical_rejection("NON_ORGANIC_OBJECT_ALERT")
                    continue
                
                if not variety_valid:
                    ai_detection_state["variety_error"] = True
                    execute_mechanical_rejection("FOREIGN_VARIETY_ABORT")
                    continue
                
                if is_defective:
                    print("🎯 DECISION: Side 1 is DEFECTIVE. Instant Rejection.")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": "NONE", "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    threading.Thread(target=operate_stopper).start()
                    while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW: time.sleep(0.05)
                    time.sleep(0.2)
                    set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
                    continue

                # --- FLIP STEP (0.5s) ---
                flip_mango_for_second_scan()
                time.sleep(0.2)
                
                # --- PROCESS SIDE 2 MATRIX ---
                is_defective, multi_mango, variety_valid, organic_valid = scan_vision_pipeline()
                
                if multi_mango:
                    ai_detection_state["multi_detection"] = True
                    execute_mechanical_rejection("MULTI_MANGO_TRAP")
                    continue
                    
                if not variety_valid:
                    ai_detection_state["variety_error"] = True
                    execute_mechanical_rejection("FOREIGN_VARIETY_ABORT")
                    continue
                
                if is_defective:
                    print("🎯 DECISION: Side 2 is DEFECTIVE. Routing to reject bin.")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": "NONE", "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    threading.Thread(target=operate_stopper).start()
                    while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW: time.sleep(0.05)
                    time.sleep(0.2)
                    set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
                    continue

                # --- RUN SIZING SECTOR SENSOR MATRIX ---
                print("🎯 DECISION: Mango skin is healthy! Starting sizing run...")
                set_conveyor_speed(CONVEYOR_SPEED, "FORWARD")
                
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
                set_machine_ready_leds()
                print("✅ Chamber clear. Ready for the next mango.")

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ ERROR in sorting loop: {e}")
            time.sleep(1)

# ==========================================
# MASTER ENTRY INVOCATION LOOP
# ==========================================
if __name__ == '__main__':
    # 1. Safely run hardware and pin configurations
    setup_hardware()

    # 2. Bind mechatronic processing components to persistent daemon loops
    sorting_thread = threading.Thread(target=autonomous_sorting_loop)
    sorting_thread.daemon = True
    sorting_thread.start()

    hopper_thread = threading.Thread(target=pulse_hopper)
    hopper_thread.daemon = True 
    hopper_thread.start()

    # 3. Fire local network proxy interfaces
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()

    print("✅ MangoPain Hardware core processing loop listening on port 5000")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Safety manual shutdown initiated...")
    finally:
        cleanup_hardware()