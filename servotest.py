import RPi.GPIO as GPIO
import time
import threading
import zmq
import json
from flask import Flask, request, jsonify
from adafruit_servokit import ServoKit
import logging

# Mute Flask's default terminal spam so you can see your hardware logs clearly
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# HARDWARE CONFIGURATION
# ==========================================
kit = ServoKit(channels=16)

SERVO_SMALL_GATE = 0
SERVO_MEDIUM_GATE = 1
SERVO_LARGE_GATE = 2

GATE_CLOSED_ANGLE = 0
GATE_OPEN_ANGLE = 90

PIN_GREEN_LED = 5
PIN_RED_LED = 6
PIN_BUZZER = 16

PIN_CONV_IN1 = 17 
PIN_CONV_IN2 = 27 

# System State Variables
machine_state = "READY"  
gate_states = {"small": "closed", "medium": "closed", "large": "closed"}

# ==========================================
# MECHATRONIC MOTOR CONTROLS
# ==========================================
def stop_conveyor():
    GPIO.output(PIN_CONV_IN1, GPIO.LOW)
    GPIO.output(PIN_CONV_IN2, GPIO.LOW)
    print("[HARDWARE] Conveyor Stopped")

def start_conveyor():
    GPIO.output(PIN_CONV_IN1, GPIO.HIGH)
    GPIO.output(PIN_CONV_IN2, GPIO.LOW) 
    print("[HARDWARE] Conveyor Started")

def reverse_conveyor():
    GPIO.output(PIN_CONV_IN1, GPIO.LOW)
    GPIO.output(PIN_CONV_IN2, GPIO.HIGH) 
    print("[HARDWARE] Conveyor Reversed")

def reset_servos_to_default():
    global gate_states
    kit.servo[SERVO_SMALL_GATE].angle = GATE_CLOSED_ANGLE
    kit.servo[SERVO_MEDIUM_GATE].angle = GATE_CLOSED_ANGLE
    kit.servo[SERVO_LARGE_GATE].angle = GATE_CLOSED_ANGLE
    gate_states = {"small": "closed", "medium": "closed", "large": "closed"}
    print("[HARDWARE] Servos reset to default closed positions")

def operate_gate(size, action):
    global gate_states
    angle = GATE_OPEN_ANGLE if action == "open" else GATE_CLOSED_ANGLE
    size_upper = size.upper()
    
    if size_upper == "SMALL":
        kit.servo[SERVO_SMALL_GATE].angle = angle
        gate_states["small"] = action
    elif size_upper == "MEDIUM":
        kit.servo[SERVO_MEDIUM_GATE].angle = angle
        gate_states["medium"] = action
    elif size_upper == "LARGE":
        kit.servo[SERVO_LARGE_GATE].angle = angle
        gate_states["large"] = action
        
    print(f"[HARDWARE] {size_upper} Gate {action.upper()}")

# ==========================================
# SYSTEM & SAFETY LOGIC
# ==========================================
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    GPIO.setup(PIN_GREEN_LED, GPIO.OUT)
    GPIO.setup(PIN_RED_LED, GPIO.OUT)
    GPIO.setup(PIN_BUZZER, GPIO.OUT)
    
    GPIO.setup(PIN_CONV_IN1, GPIO.OUT)
    GPIO.setup(PIN_CONV_IN2, GPIO.OUT)
    
    stop_conveyor()
    reset_servos_to_default()
    set_machine_ready()
    print("✓ GPIO & I2C Setup Complete. Machine is READY.")

def set_machine_ready():
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.LOW)

def set_machine_scanning():
    GPIO.output(PIN_GREEN_LED, GPIO.LOW)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)

def handle_multi_mango_error():
    global machine_state
    if machine_state == "PAUSED":
        return # Already paused
        
    print("⚠ ERROR: MULTIPLE MANGOES DETECTED BY AI!")
    machine_state = "PAUSED"
    stop_conveyor()                 
    reset_servos_to_default()       
    
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(1.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)

def execute_rejection(reason):
    global machine_state
    if machine_state == "PAUSED":
        return

    print(f"🚫 REJECTION TRIGGERED: {reason}")
    stop_conveyor()
    
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(0.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    reverse_conveyor()              
    time.sleep(2.0)                 
    start_conveyor() # Resume normal flow after ejecting

# ==========================================
# COMMUNICATION BRIDGE 1: FLASK HTTP API
# ==========================================
@app.route('/api/hardware/control', methods=['POST'])
def handle_control():
    global machine_state
    data = request.get_json() or {}
    action = data.get('action')

    if action == 'start' or action == 'continue':
        machine_state = "SCANNING"
        set_machine_scanning()
        start_conveyor()
    elif action == 'pause':
        machine_state = "PAUSED"
        stop_conveyor()
        print("⏸ System Paused via Dashboard.")
    elif action == 'stop':
        machine_state = "STOPPED"
        stop_conveyor()
        reset_servos_to_default()
        set_machine_ready()
        print("🛑 System Stopped via Dashboard.")
        
    return jsonify({'success': True, 'state': machine_state})

@app.route('/api/hardware/gate', methods=['POST', 'GET'])
def handle_gate():
    if request.method == 'GET':
        return jsonify({'gate_states': gate_states})
        
    data = request.get_json() or {}
    gate = data.get('gate')
    action = data.get('action') # "open" or "close"
    
    if gate and action:
        operate_gate(gate, action)
        return jsonify({'success': True, 'gate_states': gate_states})
    return jsonify({'success': False, 'error': 'Invalid parameters'}), 400

# ==========================================
# COMMUNICATION BRIDGE 2: ZMQ AI SUBSCRIBER
# ==========================================
def zmq_ai_listener():
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.connect("tcp://127.0.0.1:5555")
    subscriber.setsockopt_string(zmq.SUBSCRIBE, "")
    print("✓ ZMQ AI Vision Subscriber linked on port 5555.")

    while True:
        try:
            # NOBLOCK ensures the motor thread doesn't freeze waiting for the camera
            message = subscriber.recv_json(flags=zmq.NOBLOCK)
            
            # If the machine is paused/stopped, ignore AI commands
            if machine_state != "SCANNING":
                continue
                
            if message.get('multi_detection') is True:
                handle_multi_mango_error()
                
            elif message.get('is_defective') is True:
                # Trigger the rejection mechanism for defective/not-carabao mangoes
                execute_rejection("YOLO Vision Failed Health Check")

        except zmq.Again:
            # No new message from camera, wait 10ms and loop again
            time.sleep(0.01)
        except Exception as e:
            print(f"⚠ ZMQ Subscriber Error: {e}")
            time.sleep(1)

# ==========================================
# THE MASTER EXECUTION LOOP
# ==========================================
if __name__ == '__main__':
    try:
        # 1. Init Hardware
        setup_gpio()
        
        # 2. Spin up the AI Listener in the background
        ai_thread = threading.Thread(target=zmq_ai_listener, daemon=True)
        ai_thread.start()
        
        # 3. Spin up the Flask API in the background (Port 5000)
        print("✓ Flask Hardware Proxy linking on port 5000...")
        flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False), daemon=True)
        flask_thread.start()
        
        print("\n--- MangPain Master System Online ---")
        print("Waiting for Dashboard commands...")
        
        # 4. Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nForce quitting... Safing hardware.")
        stop_conveyor()
        reset_servos_to_default()
        GPIO.cleanup()