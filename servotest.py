import RPi.GPIO as GPIO
import time
import board
import busio
import threading 
import subprocess 
from ultralytics import YOLO
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

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

# --- AI Setup ---
print("Loading YOLO AI Brain...")
model = YOLO("final_weights.pt")

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
state_lock = threading.Lock()
last_mango = {"size": None, "health": None, "timestamp": None}
events_log = []

# Gate state tracking
gate_states = {
    "small": "closed",
    "medium": "closed",
    "large": "closed"
}

# ==========================================
# 4. BACKGROUND THREAD FUNCTIONS
# ==========================================
# Conveyor belt will be controlled via API commands, not auto-started
GPIO.output(R_EN, GPIO.HIGH)
GPIO.output(L_EN, GPIO.HIGH)
GPIO.output(LPWM, GPIO.LOW) 

hopper_active = True 

def pulse_hopper():
    while hopper_active:
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
        conveyor_pwm.ChangeDutyCycle(75)
        return jsonify({'success': True, 'message': 'Sorting started'})
    elif action == 'pause':
        sorting_active = False
        sorting_paused = True
        conveyor_pwm.ChangeDutyCycle(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action == 'resume':
        sorting_active = True
        sorting_paused = False
        conveyor_pwm.ChangeDutyCycle(75)
        return jsonify({'success': True, 'message': 'Sorting resumed'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        conveyor_pwm.ChangeDutyCycle(0)
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

# ==========================================
# 5. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
print("\n" + "="*45)
print("     AUTONOMOUS SORTING ACTIVE")
print("="*45)
print("Waiting for mango at the Trigger Sensor...")
print("Press Ctrl+C to cleanly shut down motors.")
print("="*45)

def autonomous_sorting_loop():
    """Main sorting loop running in background thread"""
    global sorting_active, sorting_paused, count_small, count_medium, count_large, count_defective, count_total, last_mango
    
    while True:
        try:
            if not sorting_active:
                time.sleep(0.1)
                continue
                
            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                
                is_defective = False
                
                # 1. Take a picture instantly using the native Pi terminal command with timeout
                print("📸 Snapping photo with Pi Camera...")
                try:
                    subprocess.run(["rpicam-jpeg", "-o", "current_mango.jpg", "-t", "1", "--nopreview"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    
                    # 2. Run the AI Prediction directly on the saved photo
                    print("🧠 AI Analyzing image...")
                    results = model.predict(source="current_mango.jpg", conf=0.5, verbose=False)
                    
                    # Check the results. If it finds even one "Defective" bounding box, flag it.
                    for r in results:
                        for c in r.boxes.cls:
                            class_name = model.names[int(c)] 
                            if class_name == "Defective":
                                is_defective = True
                                break
                except subprocess.TimeoutExpired:
                    print(f"⚠️ Camera timeout: rpicam-jpeg took too long. Assuming mango is Good.")
                except Exception as e:
                    print(f"⚠️ Camera error: Could not grab frame. Assuming mango is Good. Details: {e}")

                # 3. Hardware Size Scan
                print(f"📐 Scanning physical size for {SCAN_DELAY} seconds...")
                detected_size = "SMALL" 
                end_time = time.time() + SCAN_DELAY
                while time.time() < end_time:
                    if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                        detected_size = "LARGE"
                    elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                        detected_size = "MEDIUM"
                    time.sleep(0.01) 

                # 4. Fire off the stopper thread for every mango
                threading.Thread(target=operate_stopper).start() 
                
                # 5. Routing & Counting Logic
                count_total += 1
                
                if is_defective:
                    print("🎯 DECISION: Mango is DEFECTIVE. Routing to reject bin.")
                    count_defective += 1
                    # NOTE: If you have a specific servo for defective fruit, trigger its thread here!
                    
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
    
    print("Powering down Conveyor and Hopper...")
    sorting_active = False
    sorting_paused = False
    hopper_active = False 
    conveyor_pwm.stop() 
    GPIO.output(RPWM, GPIO.LOW)
    GPIO.output(LPWM, GPIO.LOW)
    GPIO.output(R_EN, GPIO.LOW)
    GPIO.output(L_EN, GPIO.LOW)
    
    rotating_gate.throttle = 0.0 
    
    barrier_gate.angle = BARRIER_LOCKED
    small_gate.angle = GATE_CLOSED
    medium_gate.angle = GATE_CLOSED
    large_gate.angle = GATE_CLOSED
    
    time.sleep(0.5) 
    pca.deinit()
    GPIO.cleanup()
    print("✅ Hardware safely powered down. Program exited.")


