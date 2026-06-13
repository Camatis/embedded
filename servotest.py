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
    """Safely stop all hardware operations."""
    global sorting_active, sorting_paused, hopper_active, conveyor_pwm
    print('🛑 Stopping all hardware...')
    try:
        sorting_active = False
        sorting_paused = False
        hopper_active = False
        
        # Stop conveyor gracefully
        if 'conveyor_pwm' in globals():
            conveyor_pwm.stop()
            GPIO.output(RPWM, GPIO.LOW)
            GPIO.output(LPWM, GPIO.LOW)
            GPIO.output(R_EN, GPIO.LOW)
            GPIO.output(L_EN, GPIO.LOW)
            print('✅ Conveyor stopped')
        
        # Stop and close all servo gates
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
        
        # Deinit I2C/PCA
        try:
            if 'pca' in globals():
                pca.deinit()
            print('✅ PCA9685 deinitialized')
        except:
            pass
        
        # Clean up GPIO
        GPIO.cleanup()
        print('✅ GPIO cleaned up')
    except Exception as e:
        print(f'⚠️  Error during cleanup: {e}')

# Register signal handlers
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

# Smooth speed control for the belt motor
CONVEYOR_SPEED = 75
CONVEYOR_RAMP_STEP = 5
CONVEYOR_RAMP_DELAY = 0.05
current_conveyor_speed = 0
conveyor_lock = threading.Lock()

def set_conveyor_speed(target_speed):
    """Ramp the conveyor speed up or down to avoid sudden speed changes."""
    global current_conveyor_speed, conveyor_state
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
        conveyor_state = "running" if target_speed > 0 else "stopped"
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

# Servo Angles
GATE_CLOSED = 70
GATE_OPEN = 0
BARRIER_LOCKED = 0
BARRIER_RELEASED = 90

# ==========================================
# HOPPER RAW PWM CONTROL
# ==========================================
HOPPER_CHANNEL  = 12
HOPPER_REST     = 512  # 180°
HOPPER_90_TICK  = 307  # 90°
HOPPER_0_TICK   = 102  # 0°
HOPPER_FULL_TRAVEL = 1.5
HOPPER_HALF_TRAVEL = 1.0

def set_hopper(ticks):
    """Directly sets the hopper servo position via raw PCA9685 PWM ticks."""
    pca.channels[HOPPER_CHANNEL].duty_cycle = int(ticks * 65535 / 4096)

def initial_startup_drop():
    """Priming sequence to get the first mango inside the chamber."""
    print("\n🚀 Priming Hopper: Rotating to 0° for First Mango...")
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_0_TICK)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    print("   [✅ First mango loaded into chamber.]")

print("Locking sorting gates to default positions...")
barrier_gate.angle = BARRIER_LOCKED
small_gate.angle = GATE_CLOSED
medium_gate.angle = GATE_CLOSED
large_gate.angle = GATE_CLOSED
time.sleep(1)

print("Aligning Hopper Servo to rest position...")
initial_startup_drop()
time.sleep(2)

# ==========================================
# 2. TIMING VARIABLES 
# ==========================================
CAMERA_SCAN_DELAY = 4.0    
SIZE_SCAN_DURATION = 3.0   
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
batch_state = "idle"  
conveyor_state = "stopped"  
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}

gate_states = {
    "small": "closed",
    "medium": "closed",
    "large": "closed"
}

multi_detection_flag = False
multi_detection_lock = threading.Lock()

GPIO.output(R_EN, GPIO.HIGH)
GPIO.output(L_EN, GPIO.HIGH)
GPIO.output(LPWM, GPIO.LOW)

# ==========================================
# FLASK API ENDPOINTS
# ==========================================
@app.route('/api/hardware/status', methods=['GET'])
def get_hardware_status():
    return jsonify({
        'counts': {'small': count_small, 'medium': count_medium, 'large': count_large, 'defective': count_defective, 'total': count_total},
        'sensors': {'trigger': GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW, 'medium': GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW, 'large': GPIO.input(IR_LARGE_PIN) == GPIO.LOW},
        'state': 'active' if sorting_active else ('paused' if sorting_paused else 'idle'),
        'batch_state': batch_state,
        'conveyor_state': conveyor_state,
        'last_mango': last_mango
    })

@app.route('/api/hardware/control', methods=['POST'])
def hardware_control():
    global sorting_active, sorting_paused, batch_state
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        threading.Thread(target=initial_startup_drop).start()
        sorting_active = True
        sorting_paused = False
        batch_state = 'running'
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting started - priming hopper'})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        batch_state = 'paused'
        set_conveyor_speed(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action == 'resume':
        sorting_active = True
        sorting_paused = False
        batch_state = 'running'
        set_conveyor_speed(CONVEYOR_SPEED)
        return jsonify({'success': True, 'message': 'Sorting resumed'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        batch_state = 'stopped'
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

@app.route('/api/hardware/gate', methods=['GET'])
def get_gate_status():
    return jsonify({
        'gate_states': gate_states,
        'gate_angles': {
            'small': small_gate.angle if hasattr(small_gate, 'angle') else None,
            'medium': medium_gate.angle if hasattr(medium_gate, 'angle') else None,
            'large': large_gate.angle if hasattr(large_gate, 'angle') else None
        }
    })

@app.route('/api/hardware/reset-counts', methods=['POST'])
def reset_counts():
    global count_small, count_medium, count_large, count_defective, count_total, last_mango
    count_small = count_medium = count_large = count_defective = count_total = 0
    last_mango = {"size": None, "health": None, "timestamp": datetime.now().isoformat()}
    return jsonify({'success': True, 'message': 'All counts reset to zero'})

@app.route('/api/hardware/detection', methods=['GET'])
def get_detection_status():
    global multi_detection_flag
    with multi_detection_lock:
        flag = multi_detection_flag
        multi_detection_flag = False
    return jsonify({'multi_detection': flag})

# ==========================================
# 4. ROUTING SYSTEMS
# ==========================================
def operate_hopper_cycle():
    """Single hopper sweep sequence to load the next piece into the system."""
    print("   🔄 HOPPER: Cycling next mango...")
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    set_hopper(HOPPER_90_TICK)
    time.sleep(HOPPER_HALF_TRAVEL)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    print("   [✓ Next mango loaded into chamber]")

def execute_defective_delivery():
    """
    DEFECTIVE ROUTE:
    Runs conveyor, unlocks barrier, keeps sizing gates shut, drops to defect bin, resets.
    """
    print("   🚨 EXECUTING DEFECTIVE DELIVERY ROUTE...")
    set_conveyor_speed(CONVEYOR_SPEED)
    
    print("   🔄 OPENING STOPPER GATE...")
    barrier_gate.angle = BARRIER_RELEASED
    
    time.sleep(LARGE_DROP_TIME)
    
    barrier_gate.angle = BARRIER_LOCKED
    print("   [✓ Stopper Locked]")
    
    operate_hopper_cycle()

def execute_small_delivery():
    """GOOD + SMALL ROUTE: Stopper and Small Gate open synchronously."""
    print("   🎯 SMALL ROUTE: Opening small gate and stopper gate simultaneously...")
    small_gate.angle = GATE_OPEN
    barrier_gate.angle = BARRIER_RELEASED
    gate_states['small'] = 'open'
    
    time.sleep(SMALL_DROP_TIME)
    
    small_gate.angle = GATE_CLOSED
    barrier_gate.angle = BARRIER_LOCKED
    gate_states['small'] = 'closed'
    print("   [✓ Small gate & Stopper closed]")
    
    operate_hopper_cycle()

def execute_medium_delivery():
    """GOOD + MEDIUM ROUTE: Stopper opens, wait for position shift, activate gate."""
    print("   🎯 MEDIUM ROUTE: Opening stopper...")
    barrier_gate.angle = BARRIER_RELEASED
    
    time.sleep(0.8) 
    print("   🎯 Opening medium gate...")
    medium_gate.angle = GATE_OPEN
    gate_states['medium'] = 'open'
    
    time.sleep(MEDIUM_DROP_TIME)
    
    medium_gate.angle = GATE_CLOSED
    barrier_gate.angle = BARRIER_LOCKED
    gate_states['medium'] = 'closed'
    print("   [✓ Medium gate & Stopper closed]")
    
    operate_hopper_cycle()

def execute_large_delivery():
    """GOOD + LARGE ROUTE: Stopper opens, wait for position shift, activate gate."""
    print("   🎯 LARGE ROUTE: Opening stopper...")
    barrier_gate.angle = BARRIER_RELEASED
    
    time.sleep(2.2) 
    print("   🎯 Opening large gate...")
    large_gate.angle = GATE_OPEN
    gate_states['large'] = 'open'
    
    time.sleep(LARGE_DROP_TIME)
    
    large_gate.angle = GATE_CLOSED
    barrier_gate.angle = BARRIER_LOCKED
    gate_states['large'] = 'closed'
    print("   [✓ Large gate & Stopper closed]")
    
    operate_hopper_cycle()

# ==========================================
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
def are_all_gates_default():
    ANGLE_TOLERANCE = 5
    return (
        abs(small_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(medium_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(large_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(barrier_gate.angle - BARRIER_LOCKED) <= ANGLE_TOLERANCE
    )

def scan_for_defect():
    print(f"🧠 Camera scanning for defects ({CAMERA_SCAN_DELAY} seconds)...")
    time.sleep(CAMERA_SCAN_DELAY)
    
    is_defective = False
    detection_received = False
    retry_count = 0
    max_retries = 40  
    
    while not detection_received and retry_count < max_retries:
        try:
            response = requests.get('http://127.0.0.1:8082/detection', timeout=2)
            if response.status_code == 200:
                detection_data = response.json()
                detections_list = detection_data.get('detections', [])
                multi_detection = detection_data.get('multi_detection', False)
                detection_received = True
                
                for d in detections_list:
                    cls_name = str(d.get('class', '')).strip().lower()
                    if 'not' not in cls_name and ('defect' in cls_name or 'bad' in cls_name or 'damaged' in cls_name or 'rotten' in cls_name):
                        is_defective = True
                
                if multi_detection:
                    with multi_detection_lock:
                        global multi_detection_flag
                        multi_detection_flag = True
            else:
                retry_count += 1
                time.sleep(0.1)
        except:
            retry_count += 1
            time.sleep(0.1)
            
    return is_defective

def flip_mango_for_second_scan():
    print("▶️  RUNNING CONVEYOR FOR 0.5 SECONDS TO FLIP MANGO...")
    set_conveyor_speed(CONVEYOR_SPEED)
    time.sleep(0.5)
    print("🛑 STOPPING CONVEYOR...")
    set_conveyor_speed(0)

def autonomous_sorting_loop():
    global sorting_active, sorting_paused, count_small, count_medium, count_large, count_defective, count_total, last_mango
    last_state = None
    
    while True:
        try:
            if not sorting_active:
                time.sleep(0.1)
                continue
            
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                
                if not are_all_gates_default():
                    time.sleep(0.1)
                    continue
                
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                print("🛑 STOPPING BELT FOR FIRST SCAN...")
                set_conveyor_speed(0)
                
                # --- SCAN SIDE 1 ---
                if scan_for_defect():
                    print("🚨 DEFECTIVE on first side → INSTANT REJECTION!")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": None, "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    
                    execute_defective_delivery()
                    print("✅ Chamber clear. Ready for next mango.\n")
                    continue
                
                # --- FLIP STEP (0.5s) ---
                print("✅ GOOD on first side → Flipping mango for second scan...")
                flip_mango_for_second_scan()
                
                # --- SCAN SIDE 2 ---
                if scan_for_defect():
                    print("🚨 DEFECTIVE on second side → INSTANT REJECTION!")
                    count_total += 1
                    count_defective += 1
                    last_mango = {"size": None, "health": "DEFECTIVE", "timestamp": datetime.now().isoformat()}
                    
                    execute_defective_delivery()
                    print("✅ Chamber clear. Ready for next mango.\n")
                    continue
                
                # --- NOT DEFECTIVE ROUTE SYSTEM ---
                print("✅ GOOD on both sides → Executing sizing track run...")
                count_total += 1
                
                print(f"▶️  STARTING BELT FOR SIZE DETECTION ({SIZE_SCAN_DURATION} seconds)...")
                set_conveyor_speed(CONVEYOR_SPEED)
                
                detected_size = "SMALL"
                end_time = time.time() + SIZE_SCAN_DURATION
                
                while time.time() < end_time:
                    if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                        detected_size = "LARGE"
                    elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                        detected_size = "MEDIUM"
                    time.sleep(0.01)
                
                print(f"📏 Size scan complete → Detected: {detected_size}")
                
                if detected_size == "SMALL":
                    count_small += 1
                    last_mango = {"size": "SMALL", "health": "GOOD", "timestamp": datetime.now().isoformat()}
                    execute_small_delivery()
                elif detected_size == "MEDIUM":
                    count_medium += 1
                    last_mango = {"size": "MEDIUM", "health": "GOOD", "timestamp": datetime.now().isoformat()}
                    execute_medium_delivery()
                elif detected_size == "LARGE":
                    count_large += 1
                    last_mango = {"size": "LARGE", "health": "GOOD", "timestamp": datetime.now().isoformat()}
                    execute_large_delivery()
                
                print("-" * 60)
                print(f"📊 LIVE COUNTS | Total: {count_total} | S: {count_small} | M: {count_medium} | L: {count_large} | Defective: {count_defective}")
                print("-" * 60)
                
                print("⏳ Waiting for mango tail to clear trigger sensor...")
                while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    time.sleep(0.05) 
                time.sleep(0.2)
                print("✅ Chamber clear. Ready for next mango.\n")

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ ERROR in sorting loop: {e}")
            time.sleep(1)

# Service thread initialization
sorting_thread = threading.Thread(target=autonomous_sorting_loop)
sorting_thread.daemon = True
sorting_thread.start()

flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True))
flask_thread.daemon = True
flask_thread.start()

print("✅ Flask API server started on port 5000")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n🛑 Ctrl+C Shutdown...")
finally:
    cleanup_hardware()