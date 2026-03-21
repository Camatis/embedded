import RPi.GPIO as GPIO
import time
import cv2
from ultralytics import YOLO
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

# Global flag for autonomous sorting
sorting_active = False
sorting_thread = None

# --- Setup GPIO ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Conveyor Motor Setup (DC Motor with PWM) ---
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

# --- IR Sensors (Size Grading) ---
IR_BOTTOM = 17  # Trigger & Small
IR_MIDDLE = 27  # Medium
IR_TOP = 22     # Large

GPIO.setup(IR_BOTTOM, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_MIDDLE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IR_TOP, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- A4988 Stepper Drivers (Sorting Gates) ---
GATES = {
    "SMALL": (5, 6),
    "MEDIUM": (13, 19),
    "LARGE": (26, 21)
}

for size, pins in GATES.items():
    GPIO.setup(pins[0], GPIO.OUT) # DIR Pin
    GPIO.setup(pins[1], GPIO.OUT) # STEP Pin

# --- Setup AI ---
print("Loading YOLO Model...")
mango_model = YOLO('final_weights.pt')
cap = cv2.VideoCapture(0)

def rotate_stepper_gate(size_category):
    """
    Swings the selected gate 70 degrees open, waits 3 seconds, and snaps shut.
    """
    if size_category not in GATES:
        return
        
    dir_pin, step_pin = GATES[size_category]
    
    steps_for_70_deg = 39 
    step_delay = 0.005
    
    print(f"⚙️ Actuating {size_category} gate...")
    
    # Swing Open
    GPIO.output(dir_pin, GPIO.HIGH)
    for _ in range(steps_for_70_deg):
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(step_delay)
        
    time.sleep(3.0) 
    
    # Swing Closed
    GPIO.output(dir_pin, GPIO.LOW)
    for _ in range(steps_for_70_deg):
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(step_delay)
        
    print(f"✅ {size_category} gate reset and locked.")

def capture_and_grade_mango():
    print("🥭 Mango detected! Rolling to center...")
    time.sleep(0.5) 
    
    size_category = "SMALL"
    if GPIO.input(IR_TOP) == GPIO.LOW:
        size_category = "LARGE"
    elif GPIO.input(IR_MIDDLE) == GPIO.LOW:
        size_category = "MEDIUM"
        
    print(f"📏 Size Locked: {size_category}")
    
    is_defective = False
    for _ in range(3):
        ret, frame = cap.read()
        if ret:
            results = mango_model.predict(frame, conf=0.6, verbose=False)
            for r in results:
                for box in r.boxes:
                    if mango_model.names[int(box.cls[0])].lower() == "defective":
                        is_defective = True
        time.sleep(0.2)
        
    health_status = "DEFECTIVE" if is_defective else "GOOD"
    return health_status, size_category

def autonomous_sorting_loop():
    global sorting_active
    print("✅ Conveyor Running. Waiting for mangoes...")
    try:
        while sorting_active:
            if GPIO.input(IR_BOTTOM) == GPIO.LOW:
                health, size = capture_and_grade_mango()
                
                print("--- FINAL RESULTS ---")
                print(f"Status: {health}")
                print(f"Size:   {size}")
                
                if health == "DEFECTIVE":
                    print("🚨 Defective Mango! Bypassing gates to the reject bin.")
                else:
                    rotate_stepper_gate(size)
                
                print("---------------------")
                time.sleep(2) 
            
            time.sleep(0.05)
    except Exception as e:
        print(f"Error in sorting loop: {e}")
    finally:
        print("Sorting loop stopped.")

# Flask Routes
@app.route('/control', methods=['POST'])
def control():
    global sorting_active, sorting_thread
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        if not sorting_active:
            sorting_active = True
            sorting_thread = threading.Thread(target=autonomous_sorting_loop)
            sorting_thread.start()
            # Start conveyor
            GPIO.output(R_EN, GPIO.HIGH)
            GPIO.output(L_EN, GPIO.HIGH)
            GPIO.output(LPWM, GPIO.LOW)
            conveyor_pwm.ChangeDutyCycle(75)
            return jsonify({'success': True, 'message': 'Sorting started'})
        else:
            return jsonify({'success': False, 'message': 'Already running'})
    
    elif action == 'stop':
        sorting_active = False
        if sorting_thread:
            sorting_thread.join(timeout=5)
        # Stop conveyor
        conveyor_pwm.ChangeDutyCycle(0)
        return jsonify({'success': True, 'message': 'Sorting stopped'})
    
    elif action == 'pause':
        sorting_active = False
        conveyor_pwm.ChangeDutyCycle(0)
        return jsonify({'success': True, 'message': 'Sorting paused'})
    
    elif action == 'continue':
        if not sorting_active:
            sorting_active = True
            sorting_thread = threading.Thread(target=autonomous_sorting_loop)
            sorting_thread.start()
            GPIO.output(R_EN, GPIO.HIGH)
            GPIO.output(L_EN, GPIO.HIGH)
            GPIO.output(LPWM, GPIO.LOW)
            conveyor_pwm.ChangeDutyCycle(75)
            return jsonify({'success': True, 'message': 'Sorting continued'})
        else:
            return jsonify({'success': False, 'message': 'Already running'})
    
    return jsonify({'success': False, 'message': 'Invalid action'})

@app.route('/gate', methods=['POST'])
def control_gate():
    data = request.get_json()
    gate = data.get('gate')
    action = data.get('action')
    
    if action == 'open' and gate in GATES:
        rotate_stepper_gate(gate)
        return jsonify({'success': True, 'message': f'Gate {gate} actuated'})
    
    return jsonify({'success': False, 'message': 'Invalid gate or action'})

@app.route('/conveyor', methods=['POST'])
def control_conveyor():
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        GPIO.output(R_EN, GPIO.HIGH)
        GPIO.output(L_EN, GPIO.HIGH)
        GPIO.output(LPWM, GPIO.LOW)
        conveyor_pwm.ChangeDutyCycle(75)
        return jsonify({'success': True, 'message': 'Conveyor started'})
    
    elif action == 'stop':
        conveyor_pwm.ChangeDutyCycle(0)
        return jsonify({'success': True, 'message': 'Conveyor stopped'})
    
    return jsonify({'success': False, 'message': 'Invalid action'})

@app.route('/sensors', methods=['GET'])
def get_sensors():
    sensors = {
        'small': GPIO.input(IR_BOTTOM) == GPIO.LOW,
        'medium': GPIO.input(IR_MIDDLE) == GPIO.LOW,
        'large': GPIO.input(IR_TOP) == GPIO.LOW,
        'defective': False,  # Not real-time, only during grading
        'detectedSize': None,
        'timestamp': time.time()
    }
    return jsonify(sensors)

if __name__ == '__main__':
    print("Starting Flask server on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=False)

