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
            # Let the belt settle cleanly before accepting the next batch
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
# PCA9685 at 50Hz = 20ms period
# 12-bit resolution = 4096 steps
# Formula: pulse_width_ms / 20ms * 4096
#
#   180° (REST)  = 2.5ms → 4096 * (2.5/20) = 512 ticks ← arm fully LEFT
#   90°  (MID)   = 1.5ms → 4096 * (1.5/20) = 307 ticks ← arm CENTER
#   0°   (FULL)  = 0.5ms → 4096 * (0.5/20) = 102 ticks ← arm fully RIGHT
#
# ⬇️ TUNE THESE if rotation is still off (±20 ticks)

HOPPER_CHANNEL  = 12
HOPPER_REST     = 512  # 180° — arm fully LEFT, resting position
HOPPER_90_TICK  = 307  # 90°  — arm CENTER, pipeline drop
HOPPER_0_TICK   = 102  # 0°   — arm fully RIGHT, first prime drop
HOPPER_FULL_TRAVEL = 1.5  # Seconds to complete a 180° sweep
HOPPER_HALF_TRAVEL = 1.0  # Seconds to complete a 90° sweep

def set_hopper(ticks):
    """Directly sets the hopper servo position via raw PCA9685 PWM ticks."""
    pca.channels[HOPPER_CHANNEL].duty_cycle = int(ticks * 65535 / 4096)

def initial_startup_drop():
    """
    Priming sequence:
    - Hopper rests at 180° (arm fully LEFT)
    - Rotates to 0° (arm fully RIGHT) to give the first mango
    - Resets back to 180° (bearing slips, won't pull mango back)
    """
    print("\n🚀 Priming Hopper: Rotating to 0° for First Mango...")
    # Confirm at rest (180°) before sweeping
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    # Sweep to 0° (arm fully RIGHT) to give first mango
    set_hopper(HOPPER_0_TICK)
    time.sleep(HOPPER_FULL_TRAVEL)
    # Reset back to 180° (bearing slips)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    print("   [✅ First mango loaded into chamber.]")

def operate_stopper_and_hopper():
    """
    Pipeline sequence (runs in separate thread):
    1. Open stopper → release scanned mango onto belt.
    2. Close stopper.
    3. Rotate hopper from 180° to 90° → drop next mango into chamber.
    4. Reset hopper back to 180°.
    """
    print("   🔄 PIPELINE: Opening Stopper Gate...")
    # 1. Open stopper
    barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    # 2. Close stopper
    barrier_gate.angle = BARRIER_LOCKED
    print("   [🚧 Stopper Gate locked]")
    time.sleep(1.0)  # Let stopper fully close before hopper moves
    # 3. Confirm hopper is at 180° rest before sweeping
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    # 4. Rotate to 90° (arm CENTER) to push next mango into chamber
    print("   🔄 Hopper rotating to 90°...")
    set_hopper(HOPPER_90_TICK)
    time.sleep(HOPPER_HALF_TRAVEL)
    # 5. Reset back to 180° (bearing slips)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    print("   [✅ Next mango loaded into chamber.]")

print("Locking sorting gates to default positions...")
barrier_gate.angle = BARRIER_LOCKED
small_gate.angle = GATE_CLOSED
medium_gate.angle = GATE_CLOSED
large_gate.angle = GATE_CLOSED
time.sleep(1)

print("Aligning Hopper Servo to rest position (180° / arm fully LEFT)...")
initial_startup_drop()
time.sleep(2)

# --- Detection Configuration ---
# Now using HTTP GET from webrtc_stream for simpler, more reliable detection
# No longer using ZMQ subscriptions

# ==========================================
# 2. TIMING VARIABLES 
# ==========================================
CAMERA_SCAN_DELAY = 4.0    # Belt stopped: Camera scans mango for defect (4 sec) - increased for AI processing
SIZE_SCAN_DURATION = 3.0   # Belt running: Size sensors scan mango (3 sec)
SMALL_DROP_TIME = 2.0      # Time gate stays open for small mango to drop
MEDIUM_DROP_TIME = 2.3     # Time gate stays open for medium mango to drop
LARGE_DROP_TIME = 3.5      # Time gate stays open for large mango to drop
STOPPER_DELAY = 1.5        # Time to fully open/close stopper gate 

# ==========================================
# 3. LIVE COUNTERS & STATE
# ==========================================
count_small = 0
count_medium = 0
count_large = 0
count_defective = 0
count_total = 0

# Global control state
sorting_active = False
sorting_paused = False
batch_state = "idle"  # idle, running, paused, stopped
conveyor_state = "stopped"  # stopped or running
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}
events_log = []

# Gate state tracking
gate_states = {
    "small": "closed",
    "medium": "closed",
    "large": "closed"
}

# Detection tracking for multi-mango alert
multi_detection_flag = False
multi_detection_lock = threading.Lock()

# ==========================================
# 4. BACKGROUND THREAD FUNCTIONS
# ==========================================
# Conveyor belt will be controlled via API commands, not auto-started
GPIO.output(R_EN, GPIO.HIGH)
GPIO.output(L_EN, GPIO.HIGH)
GPIO.output(LPWM, GPIO.LOW)

# ==========================================
# FLASK API ENDPOINTS
# ==========================================
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
        # Prime the hopper in a separate thread so UI doesn't hang
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
                print(f"🎯 [MANUAL] Small gate opened")
            elif gate_name == 'MEDIUM':
                medium_gate.angle = GATE_OPEN
                gate_states['medium'] = 'open'
                print(f"🎯 [MANUAL] Medium gate opened")
            elif gate_name == 'LARGE':
                large_gate.angle = GATE_OPEN
                gate_states['large'] = 'open'
                print(f"🎯 [MANUAL] Large gate opened")
            return jsonify({'success': True, 'message': f'{gate_name} gate opened', 'gate_states': gate_states})
        elif action == 'close':
            if gate_name == 'SMALL':
                small_gate.angle = GATE_CLOSED
                gate_states['small'] = 'closed'
                print(f"🎯 [MANUAL] Small gate closed")
            elif gate_name == 'MEDIUM':
                medium_gate.angle = GATE_CLOSED
                gate_states['medium'] = 'closed'
                print(f"🎯 [MANUAL] Medium gate closed")
            elif gate_name == 'LARGE':
                large_gate.angle = GATE_CLOSED
                gate_states['large'] = 'closed'
                print(f"🎯 [MANUAL] Large gate closed")
            return jsonify({'success': True, 'message': f'{gate_name} gate closed', 'gate_states': gate_states})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    
    return jsonify({'success': False, 'message': 'Invalid gate or action'})

@app.route('/api/hardware/gate', methods=['GET'])
def get_gate_status():
    """Get current status of all gates"""
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
    """Reset all batch counts to zero for a new batch"""
    global count_small, count_medium, count_large, count_defective, count_total, last_mango
    
    count_small = 0
    count_medium = 0
    count_large = 0
    count_defective = 0
    count_total = 0
    last_mango = {"size": None, "health": None, "timestamp": None}
    
    print("✓ Batch counts reset to zero")
    return jsonify({
        'success': True,
        'message': 'All counts reset to zero',
        'counts': {
            'small': count_small,
            'medium': count_medium,
            'large': count_large,
            'defective': count_defective,
            'total': count_total
        }
    })

@app.route('/api/hardware/detection', methods=['GET'])
def get_detection_status():
    """Get multi-detection alert status"""
    global multi_detection_flag
    with multi_detection_lock:
        flag = multi_detection_flag
        multi_detection_flag = False  # Reset after reading
    return jsonify({
        'multi_detection': flag
    })

def operate_stopper_release():
    """
    Release stopper gate to allow mango to exit.
    Used for both defective and good mangoes after routing is determined.
    """
    print("   🔄 OPENING STOPPER GATE...")
    barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    barrier_gate.angle = BARRIER_LOCKED
    print("   [✓ Stopper Locked]")

def operate_hopper_cycle():
    """
    Single hopper cycle: Rotate from 180° to 90° and back.
    Loads the next mango into the chamber.
    Called after each mango exits the chamber.
    """
    print("   🔄 HOPPER: Rotating to load next mango...")
    # Confirm hopper is at 180° rest before sweeping
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    # Rotate to 90° (arm CENTER) to push next mango into chamber
    set_hopper(HOPPER_90_TICK)
    time.sleep(HOPPER_HALF_TRAVEL)
    # Reset back to 180° (bearing slips)
    set_hopper(HOPPER_REST)
    time.sleep(HOPPER_FULL_TRAVEL)
    print("   [✓ Next mango loaded into chamber]")

def route_defective():
    """
    DEFECTIVE ROUTE:
    1. Stopper already opened by main loop (belt already running)
    2. Mango travels on belt without any size gates opening (rejected)
    3. Wait for mango to clear, then cycle hopper for next batch
    """
    print("   🚨 DEFECTIVE ROUTE: Rejecting mango to bin...")
    
    # Wait for defective mango to travel to reject bin
    # (approximately the time for largest mango to travel)
    print("   ⏳ Waiting for defective mango to clear...")
    time.sleep(LARGE_DROP_TIME)
    
    # Cycle hopper to load next mango
    operate_hopper_cycle()
    print("   [✓ Defective rejection complete]")

def route_small():
    """
    GOOD + SMALL ROUTE:
    1. Stopper already opened
    2. Open small gate for mango to drop
    3. Close gate after drop time
    4. Cycle hopper for next batch
    """
    print("   🎯 SMALL ROUTE: Opening small gate...")
    small_gate.angle = GATE_OPEN
    gate_states['small'] = 'open'
    time.sleep(SMALL_DROP_TIME) 
    small_gate.angle = GATE_CLOSED
    gate_states['small'] = 'closed'
    print("   [✓ Small gate closed]")
    
    # Cycle hopper to load next mango
    operate_hopper_cycle()

def route_medium():
    """
    GOOD + MEDIUM ROUTE:
    1. Stopper already opened
    2. Wait for mango to travel to medium bin position
    3. Open medium gate for mango to drop
    4. Close gate after drop time
    5. Cycle hopper for next batch
    """
    # Small bin comes first, so wait before medium gate
    print("   🎯 MEDIUM ROUTE: Waiting for belt position...")
    time.sleep(0.8)  # Time for mango to pass small bin
    
    print("   🎯 Opening medium gate...")
    medium_gate.angle = GATE_OPEN
    gate_states['medium'] = 'open'
    time.sleep(MEDIUM_DROP_TIME) 
    medium_gate.angle = GATE_CLOSED
    gate_states['medium'] = 'closed'
    print("   [✓ Medium gate closed]")
    
    # Cycle hopper to load next mango
    operate_hopper_cycle()

def route_large():
    """
    GOOD + LARGE ROUTE:
    1. Stopper already opened
    2. Wait for mango to travel to large bin position
    3. Open large gate for mango to drop
    4. Close gate after drop time
    5. Cycle hopper for next batch
    """
    # Small and medium bins come first, so wait before large gate
    print("   🎯 LARGE ROUTE: Waiting for belt position...")
    time.sleep(2.2)  # Time for mango to pass small and medium bins
    
    print("   🎯 Opening large gate...")
    large_gate.angle = GATE_OPEN
    gate_states['large'] = 'open'
    time.sleep(LARGE_DROP_TIME) 
    large_gate.angle = GATE_CLOSED
    gate_states['large'] = 'closed'
    print("   [✓ Large gate closed]")
    
    # Cycle hopper to load next mango
    operate_hopper_cycle()

# ==========================================
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
print("\n" + "="*45)
print("✅ SERVOTEST HARDWARE SERVICE READY")
print("="*45)
print("Waiting for sorting START command...")
print("Press Ctrl+C to cleanly shut down motors.")
print("="*45)

def are_all_gates_default():
    """Check if all gates are in their default (closed/locked) positions with tolerance"""
    ANGLE_TOLERANCE = 5  # Allow ±5 degrees tolerance for servo precision
    gates_ok = (
        abs(small_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(medium_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(large_gate.angle - GATE_CLOSED) <= ANGLE_TOLERANCE and
        abs(barrier_gate.angle - BARRIER_LOCKED) <= ANGLE_TOLERANCE
    )
    return gates_ok

def scan_for_defect():
    """
    Scan one side of mango for defects by polling webrtc_stream's latest detection.
    Returns is_defective (bool)
    """
    print(f"🧠 Camera scanning for defects ({CAMERA_SCAN_DELAY} seconds)...")
    time.sleep(CAMERA_SCAN_DELAY)
    
    is_defective = False
    detection_received = False
    retry_count = 0
    max_retries = 40  # 40 * 100ms = 4 seconds
    
    print(f"   📡 Polling webrtc_stream for detection (max {max_retries} retries)...")
    
    while not detection_received and retry_count < max_retries:
        try:
            # HTTP GET latest detection from webrtc_stream
            response = requests.get('http://127.0.0.1:8082/detection', timeout=2)
            
            if response.status_code == 200:
                detection_data = response.json()
                
                print(f"   ✅ HTTP DETECTION RECEIVED:")
                print(f"      Raw: {detection_data}")
                
                # Extract defect status
                is_defective_field = detection_data.get('is_defective', None)
                multi_detection = detection_data.get('multi_detection', False)
                detections_list = detection_data.get('detections', [])
                
                # Parse is_defective
                is_defective = bool(is_defective_field)
                
                detection_received = True
                
                print(f"      ✓ PARSED: is_defective_field={is_defective_field} -> is_defective={is_defective}")
                print(f"      ✓ detections_count={len(detections_list)}, multi_detection={multi_detection}")
                
                if len(detections_list) > 0:
                    classes = [d.get('class', 'unknown') for d in detections_list]
                    print(f"      ✓ Detection classes: {classes}")
                
                if is_defective:
                    print(f"      🚨🚨🚨 DEFECTIVE DETECTED - WILL ROUTE TO DEFECTIVE BIN")
                else:
                    print(f"      ✅ Good mango - will proceed to next stage")
                    
                if multi_detection:
                    print("      ⚠️ WARNING: MULTIPLE MANGOES DETECTED!")
                    with multi_detection_lock:
                        global multi_detection_flag
                        multi_detection_flag = True
                        
            else:
                # Bad response status, retry
                retry_count += 1
                if retry_count % 10 == 0:
                    print(f"      ⏳ Retry {retry_count}/{max_retries} - got HTTP {response.status_code}")
                if retry_count < max_retries:
                    time.sleep(0.1)
                    
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            # Timeout or connection error, retry
            retry_count += 1
            if retry_count % 10 == 0:
                print(f"      ⏳ Retry {retry_count}/{max_retries} - {type(e).__name__}")
            if retry_count < max_retries:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"      ❌ Exception: {type(e).__name__}: {e}")
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(0.1)
    
    if not detection_received:
        print(f"   ⏱️ TIMEOUT: No valid detection received after {retry_count*100}ms")
        print(f"   ⚠️ webrtc_stream may not be running or responding")
        is_defective = False
    
    print(f"   📤 RETURNING: is_defective={is_defective}")
    return is_defective

def flip_mango_for_second_scan():
    """
    Run conveyor for 1 second to flip mango for second scan.
    Mango stays in chamber.
    """
    print("▶️  RUNNING CONVEYOR FOR 1 SECOND TO FLIP MANGO...")
    set_conveyor_speed(CONVEYOR_SPEED)
    time.sleep(1.0)
    print("🛑 STOPPING CONVEYOR...")
    set_conveyor_speed(0)

def autonomous_sorting_loop():
    """
    Main autonomous sorting loop (runs in background thread).
    
    Multi-stage workflow:
    
    STAGE 1 - FIRST DEFECT SCAN (Belt Stopped):
      1. Mango detected at trigger sensor
      2. Check if all gates are in default position before stopping belt
      3. Belt stops, stopper gate stays locked
      4. Camera scans first side for defects
      5. If DEFECTIVE → proceed to defective delivery
      6. If GOOD → proceed to stage 2
    
    STAGE 2 - FLIP MANGO (Belt Running for 1 sec):
      1. Conveyor runs for 1 second to flip mango
      2. Conveyor stops
      3. Camera scans second side for defects
      4. If DEFECTIVE → proceed to defective delivery
      5. If GOOD → proceed to stage 3
    
    STAGE 3 - SIZE DETECTION (Belt Running):
      1. Conveyor runs for 3 seconds
      2. Size sensors scan IR levels while mango rolls
      3. Stopper opens, mango routes to size-specific bin
    
    DEFECTIVE DELIVERY:
      - Conveyor runs, stopper opens
      - No size gates open
      - Mango travels to defective bin
    
    NOT DEFECTIVE DELIVERY:
      - Conveyor runs for 3 seconds while size sensors scan
      - Stopper opens
      - Mango routed to small/medium/large bin based on size
    """
    global sorting_active, sorting_paused, count_small, count_medium, count_large, count_defective, count_total, last_mango
    last_state = None
    
    while True:
        try:
            # Log state changes
            current_state = 'active' if sorting_active else ('paused' if sorting_paused else 'waiting')
            if current_state != last_state:
                if current_state == 'active':
                    print("\n🔴 SORTING STARTED - Waiting for mango at Trigger Sensor...")
                elif current_state == 'paused':
                    print("\n⏸️  SORTING PAUSED")
                else:
                    print("\n⏹️  SORTING IDLE - Waiting for start command...")
                last_state = current_state
            
            if not sorting_active:
                time.sleep(0.1)
                continue
            
            # ═══════════════════════════════════════════════════════════════
            # STAGE 1: FIRST DEFECT SCAN (Belt Stopped)
            # ═══════════════════════════════════════════════════════════════
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                
                # Check if all gates are in default position before stopping
                if not are_all_gates_default():
                    print("⚠️ NOT ALL GATES IN DEFAULT POSITION - Waiting for gates to return to default...")
                    time.sleep(0.5)
                    continue
                
                # 1a. STOP BELT IMMEDIATELY
                print("🛑 STOPPING BELT FOR FIRST SCAN...")
                set_conveyor_speed(0)
                # Stopper gate is already LOCKED (default state)
                print("   [✓ Stopper gate locked]")
                
                # 1b. SCAN FIRST SIDE FOR DEFECTS
                is_defective_side1 = scan_for_defect()
                
                detected_size = "SMALL"  # Default fallback
                is_defective = False
                
                if is_defective_side1:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # DEFECTIVE ON FIRST SCAN - IMMEDIATE DELIVERY
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    print("🚨 DEFECTIVE on first side → INSTANT DEFECTIVE DELIVERY!")
                    is_defective = True
                    count_total += 1
                    count_defective += 1
                    
                    # Start belt and open stopper immediately
                    print("▶️  STARTING BELT...")
                    set_conveyor_speed(CONVEYOR_SPEED)
                    print("🎯 DECISION: DEFECTIVE → Opening stopper immediately")
                    
                    # Fire stopper and route in background thread
                    threading.Thread(target=operate_stopper_release).start()
                    threading.Thread(target=route_defective).start()
                    
                else:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # GOOD ON FIRST SCAN - FLIP AND RESCAN
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    print("✅ GOOD on first side → Flipping mango for second scan...")
                    
                    # ═══════════════════════════════════════════════════════════════
                    # STAGE 2: FLIP MANGO FOR SECOND DEFECT SCAN
                    # ═══════════════════════════════════════════════════════════════
                    flip_mango_for_second_scan()
                    
                    # ═══════════════════════════════════════════════════════════════
                    # STAGE 2b: SECOND DEFECT SCAN
                    # ═══════════════════════════════════════════════════════════════
                    is_defective_side2 = scan_for_defect()
                    
                    if is_defective_side2:
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # DEFECTIVE ON SECOND SCAN - IMMEDIATE DELIVERY
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print("🚨 DEFECTIVE on second side → INSTANT DEFECTIVE DELIVERY!")
                        is_defective = True
                        count_total += 1
                        count_defective += 1
                        
                        # Start belt and open stopper immediately
                        print("▶️  STARTING BELT...")
                        set_conveyor_speed(CONVEYOR_SPEED)
                        print("🎯 DECISION: DEFECTIVE → Opening stopper immediately")
                        
                        # Fire stopper and route in background thread
                        threading.Thread(target=operate_stopper_release).start()
                        threading.Thread(target=route_defective).start()
                        
                    else:
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # GOOD ON BOTH SIDES - SIZE DETECTION PHASE
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print("✅ GOOD on both sides → Proceeding to size detection...")
                        is_defective = False
                        count_total += 1
                        
                        # ═══════════════════════════════════════════════════════════════
                        # STAGE 3: SIZE DETECTION (Belt Running for 3 seconds)
                        # ═══════════════════════════════════════════════════════════════
                        print(f"▶️  STARTING BELT FOR SIZE DETECTION ({SIZE_SCAN_DURATION} seconds, stopper locked)...")
                        set_conveyor_speed(CONVEYOR_SPEED)
                        
                        # Scan size sensors while belt is running and mango is on belt
                        # Start with SMALL as default
                        detected_size = "SMALL"
                        end_time = time.time() + SIZE_SCAN_DURATION
                        
                        while time.time() < end_time:
                            # Priority: LARGE > MEDIUM > SMALL (most restrictive wins)
                            if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                                detected_size = "LARGE"
                            elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                                detected_size = "MEDIUM"
                            time.sleep(0.01)
                        
                        print(f"📏 Size scan complete → Detected: {detected_size}")
                        print(f"🎯 DECISION: GOOD + {detected_size}")
                        
                        # Update size-specific count
                        if detected_size == "SMALL":
                            count_small += 1
                        elif detected_size == "MEDIUM":
                            count_medium += 1
                        elif detected_size == "LARGE":
                            count_large += 1
                        
                        # Now open stopper and route based on size
                        # All these operations happen in parallel threads
                        threading.Thread(target=operate_stopper_release).start()
                        
                        if detected_size == "SMALL":
                            threading.Thread(target=route_small).start()
                        elif detected_size == "MEDIUM":
                            threading.Thread(target=route_medium).start()
                        elif detected_size == "LARGE":
                            threading.Thread(target=route_large).start()
                
                # Update last mango status
                last_mango = {
                    "size": detected_size if not is_defective else None,
                    "health": "DEFECTIVE" if is_defective else "GOOD",
                    "timestamp": datetime.now().isoformat()
                }
                
                # Print live stats
                print("-" * 60)
                print(f"📊 LIVE COUNTS | Total: {count_total} | S: {count_small} | M: {count_medium} | L: {count_large} | Defective: {count_defective}")
                print("-" * 60)
                
                # 2c. CLEAR CHAMBER FOR NEXT BATCH
                print("⏳ Waiting for mango tail to clear trigger sensor...")
                while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    time.sleep(0.05) 
                time.sleep(0.2)  # Debounce buffer
                
                print("✅ Chamber clear. Ready for next mango.\n")

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ ERROR in sorting loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)

# Detection now uses HTTP GET from webrtc_stream (no ZMQ initialization needed)
print(f"✓ Detection polling configured to use HTTP GET from webrtc_stream on port 8082")

# Start sorting loop in background thread
sorting_thread = threading.Thread(target=autonomous_sorting_loop)
sorting_thread.daemon = True
sorting_thread.start()

# Start Flask API server in background thread
flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True))
flask_thread.daemon = True
flask_thread.start()

print("✅ Flask API server started on port 5000")

try:
    # Keep main thread alive
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n🛑 Ctrl+C Detected! Commencing safety shutdown...")

finally:
    print("\n📊 FINAL SORTING REPORT:")
    print(f"Total Processed: {count_total}")
    print(f"Small: {count_small} | Medium: {count_medium} | Large: {count_large} | Defective: {count_defective}")
    print("="*30)
    
    cleanup_hardware()
    
    print("\n✅ Hardware safely powered down. Program exited.")