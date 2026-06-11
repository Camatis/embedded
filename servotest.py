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

# --- Shared ZMQ Detection (subscribe to webrtc_stream) ---
zmq_context = None
detection_subscriber = None
DETECTION_PORT = 5555
last_detection_data = None
detection_lock = threading.Lock()

# ==========================================
# 2. TIMING VARIABLES 
# ==========================================
SCAN_DELAY = 2.0      
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

def operate_stopper():
    """Release stopper only (hopper managed separately)."""
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

def route_defective():
    """Reject defective mango - no gates open, stays on belt for travel time"""
    print("   [Defective Mango Rejected - No Gates Opening]")
    print("   ⏳ Mango will travel on belt for rejection...")
    # Wait for the time it would take for a large mango to travel (longest route)
    time.sleep(TIME_TO_LARGE + LARGE_DROP_TIME)
    print("   [✅ Defective mango rejection complete]")

# ==========================================
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
print("\n" + "="*45)
print("✅ SERVOTEST HARDWARE SERVICE READY")
print("="*45)
print("Waiting for sorting START command...")
print("Press Ctrl+C to cleanly shut down motors.")
print("="*45)

def autonomous_sorting_loop():
    """Main sorting loop running in background thread"""
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
                
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                
                is_defective = False
                
                # Get defect analysis from webrtc_stream (via ZMQ subscription)
                # No need to capture our own image - webrtc_stream is already doing continuous detection
                print("🧠 Checking defect status from camera detection...")
                
                # Give camera time to detect the mango (IR sensor triggers faster than camera frame processing)
                time.sleep(0.9)  # Wait 900ms for webrtc_stream to see and process the mango
                
                is_defective = False
                
                # Wait up to 2 seconds for webrtc_stream to process and publish detection
                # Keep polling for a non-empty detection (first one wins)
                detection_received = False
                retry_count = 0
                max_retries = 20  # 20 * 100ms = 2 seconds max wait
                
                while not detection_received and retry_count < max_retries:
                    try:
                        if detection_subscriber is not None:
                            try:
                                detection_data = detection_subscriber.recv_json(flags=zmq.NOBLOCK)
                                print(f"   📊 ZMQ received: {detection_data}")
                                
                                with detection_lock:
                                    last_detection_data = detection_data
                                
                                # Check if defective
                                is_defective = detection_data.get('is_defective', False)
                                multi_detection = detection_data.get('multi_detection', False)
                                detections_list = detection_data.get('detections', [])
                                
                                # Accept this detection result (empty or not) - this is the latest
                                detection_received = True
                                
                                print(f"   ✓ is_defective={is_defective}, multi_detection={multi_detection}, detections_count={len(detections_list)}")
                                
                                if is_defective:
                                    print(f"🚨 Defective mango detected!")
                                if multi_detection:
                                    print("⚠️ MULTIPLE MANGOES DETECTED!")
                                    with multi_detection_lock:
                                        global multi_detection_flag
                                        multi_detection_flag = True
                            except zmq.Again:
                                # No message yet, wait and retry
                                retry_count += 1
                                if retry_count % 5 == 0:
                                    print(f"   ⏳ Waiting for detection... ({retry_count*100}ms elapsed)")
                                if retry_count < max_retries:
                                    time.sleep(0.1)  # Wait 100ms before retrying
                        else:
                            print("⚠️ Detection service not available, assuming mango is Good")
                            detection_received = True
                    except Exception as e:
                        print(f"⚠️ Detection error: {e}, assuming Good")
                        detection_received = True
                
                if not detection_received:
                    print(f"⚠️ Detection timeout after {retry_count*100}ms, using last known result or assuming Good")

                # 3. Hardware Size Scan (ONLY if NOT defective)
                if is_defective:
                    print("⏭️  SKIPPING size scan - mango is defective")
                    detected_size = None  # Not needed for reject route
                else:
                    print(f"📐 Scanning physical size for {SCAN_DELAY} seconds...")
                    detected_size = "SMALL" 
                    end_time = time.time() + SCAN_DELAY
                    while time.time() < end_time:
                        if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                            detected_size = "LARGE"
                        elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                            detected_size = "MEDIUM"
                        time.sleep(0.01)

                # 4. Fire off the stopper and hopper thread for every mango
                threading.Thread(target=operate_stopper_and_hopper).start() 
                
                # 5. Routing & Counting Logic
                count_total += 1
                
                if is_defective:
                    print("🎯 DECISION: Mango is DEFECTIVE. Routing to reject bin.")
                    count_defective += 1
                    threading.Thread(target=route_defective).start()
                    
                else:
                    print(f"🎯 DECISION: Mango is Good. Classified as {detected_size}.")
                    if detected_size == "SMALL":
                        count_small += 1
                        threading.Thread(target=route_small).start()
                    elif detected_size == "MEDIUM":
                        count_medium += 1
                        threading.Thread(target=route_medium).start()
                    elif detected_size == "LARGE":
                        count_large += 1
                        threading.Thread(target=route_large).start()
                
                # Update last mango
                last_mango = {
                    "size": detected_size,
                    "health": "DEFECTIVE" if is_defective else "GOOD",
                    "timestamp": datetime.now().isoformat()
                }
                
                # 6. Live Dashboard Print
                print("-" * 50)
                print(f"📊 LIVE COUNTS | Total: {count_total} | S: {count_small} | M: {count_medium} | L: {count_large} | Defective: {count_defective}")
                print("-" * 50)
                
                # 7. Phantom Mango Fix
                print("⏳ Waiting for the tail-end of the mango to clear the trigger...")
                while GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    time.sleep(0.05) 
                time.sleep(0.2) # Debounce buffer
                
                print("✅ Chamber clear. Ready for the next mango.")

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ ERROR in sorting loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)  # Prevent spam if error keeps happening

# Initialize ZMQ detection subscriber (listening to webrtc_stream)
try:
    zmq_context = zmq.Context()
    detection_subscriber = zmq_context.socket(zmq.SUB)
    detection_subscriber.setsockopt(zmq.RCVHWM, 1)  # Keep only latest message
    detection_subscriber.connect(f"tcp://127.0.0.1:{DETECTION_PORT}")
    detection_subscriber.subscribe(b"")  # Subscribe to all messages
    print(f"✓ ZMQ detection subscriber initialized on tcp://127.0.0.1:{DETECTION_PORT}")
    time.sleep(0.5)  # Give time for connection to establish
except Exception as e:
    print(f"⚠️ Failed to initialize ZMQ: {e}")
    print("⚠️ servotest will attempt sorting without defect detection")
    detection_subscriber = None

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


