#!/usr/bin/env python3
"""
Hardware Controller Wrapper
Controls MangoSort hardware via start/stop flags
Runs as a managed subprocess from Node.js backend
"""

import RPi.GPIO as GPIO
import time
import board
import busio
import threading
import subprocess 
import json
import os
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from ultralytics import YOLO

# ==========================================
# CONTROL FLAGS
# ==========================================
CONTROL_FILE = "/tmp/mangosort_control.json"
hardware_running = False
hopper_active = False

def load_control_state():
    """Load start/stop state from control file"""
    try:
        if os.path.exists(CONTROL_FILE):
            with open(CONTROL_FILE, 'r') as f:
                data = json.load(f)
                return data.get("running", False)
    except:
        pass
    return False

def save_control_state(state):
    """Save current state to control file"""
    with open(CONTROL_FILE, 'w') as f:
        json.dump({"running": state}, f)

# ==========================================
# 1. SETUP HARDWARE & AI
# ==========================================
print("Initializing Hardware...")
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Load YOLO Model ---
print("Loading AI Brain (final weights.pt)...")
model = YOLO("final weights.pt")
DEFECTIVE_CLASS_NAME = "defective" 

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

# ==========================================
# 2. TIMING VARIABLES
# ==========================================
SCAN_DELAY = 3.0      
TIME_TO_MEDIUM = 0.8  
TIME_TO_LARGE = 2.2   
SMALL_DROP_TIME = 2.0
MEDIUM_DROP_TIME = 2.3 
LARGE_DROP_TIME = 3.5  
STOPPER_DELAY = 1.5 

# ==========================================
# 3. BACKGROUND THREADS & AI FUNCTION
# ==========================================
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

def operate_stopper():
    barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    barrier_gate.angle = BARRIER_LOCKED
    print("   [Stopper Gate safely locked behind mango]")

ai_memory = {"is_defective": False, "finished": False}

def run_ai_check():
    print("   📸 Snapping photo with Pi Camera...")
    subprocess.run(['rpicam-jpeg', '-o', 'current_mango.jpg', '-t', '500', '--nopreview'], 
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("   🧠 AI is thinking...")
    results = model('current_mango.jpg', conf=0.5, verbose=False)
    
    defect_found = False
    for r in results:
        for box in r.boxes:
            class_id = int(box.cls[0])
            class_name = model.names[class_id]
            if class_name.lower() == DEFECTIVE_CLASS_NAME.lower():
                defect_found = True
                break 
                
    ai_memory["is_defective"] = defect_found
    ai_memory["finished"] = True

def start_conveyor():
    """Start the conveyor belt"""
    print("Starting Conveyor Belt (RIGHT / FORWARD at 75% Speed)...")
    GPIO.output(R_EN, GPIO.HIGH)
    GPIO.output(L_EN, GPIO.HIGH)
    GPIO.output(LPWM, GPIO.LOW) 
    conveyor_pwm.ChangeDutyCycle(100) 
    time.sleep(0.2)                   
    conveyor_pwm.ChangeDutyCycle(75)

def stop_conveyor():
    """Stop the conveyor belt"""
    print("Stopping Conveyor Belt...")
    conveyor_pwm.stop() 
    GPIO.output(RPWM, GPIO.LOW)
    GPIO.output(LPWM, GPIO.LOW)
    GPIO.output(R_EN, GPIO.LOW)
    GPIO.output(L_EN, GPIO.LOW)

def lock_gates():
    """Lock all gates to safe position"""
    barrier_gate.angle = BARRIER_LOCKED
    small_gate.angle = GATE_CLOSED
    medium_gate.angle = GATE_CLOSED
    large_gate.angle = GATE_CLOSED

# ==========================================
# 4. MAIN SORTING LOOP (Ctrl via Control File)
# ==========================================
print("\n" + "="*45)
print("     HARDWARE CONTROLLER READY")
print("="*45)
print("Waiting for start signal from web app...")
print("Press Ctrl+C to cleanly shut down.")
print("="*45)

try:
    save_control_state(False)  # Start in stopped state
    
    while True:
        hardware_running = load_control_state()
        
        if hardware_running:
            # START THE SORTING LOOP
            hopper_active = True
            start_conveyor()
            
            print("Waiting for mango at the Trigger Sensor...")
            hopper_thread = threading.Thread(target=pulse_hopper)
            hopper_thread.daemon = True 
            hopper_thread.start()
            
            while hardware_running:
                hardware_running = load_control_state()
                
                if not hardware_running:
                    break
                
                if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                    print("\n🥭 MANGO DETECTED IN CHAMBER!")
                    time.sleep(0.2)
                    
                    ai_memory["finished"] = False
                    threading.Thread(target=run_ai_check).start()
                    
                    detected_size = "SMALL"
                    end_time = time.time() + SCAN_DELAY
                    
                    print(f"   📐 Actively tracking physical size for {SCAN_DELAY} seconds...")
                    while time.time() < end_time:
                        if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                            detected_size = "LARGE"
                        elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                            detected_size = "MEDIUM"
                        time.sleep(0.01)
                    
                    while not ai_memory["finished"]:
                        time.sleep(0.01)

                    if ai_memory["is_defective"]:
                        print("🚫 DEFECTIVE MANGO DETECTED! Bypassing all sorting gates.")
                        print("1. Releasing Stopper to clear the chamber...")
                        threading.Thread(target=operate_stopper).start() 
                        
                        print("   Waiting for defective mango to physically clear the sensors...")
                        while (GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW or 
                               GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW or 
                               GPIO.input(IR_LARGE_PIN) == GPIO.LOW):
                            time.sleep(0.01)
                        
                    else:
                        print(f"✅ CLEAN MANGO. Classified as {detected_size}.")
                        if detected_size == "SMALL":
                            print("1. Releasing Stopper...")
                            threading.Thread(target=operate_stopper).start() 
                            print("2. Opening Small Gate...")
                            small_gate.angle = GATE_OPEN
                            time.sleep(SMALL_DROP_TIME) 
                            print("3. Closing Small Gate...")
                            small_gate.angle = GATE_CLOSED
                            
                        elif detected_size == "MEDIUM":
                            print("1. Releasing Stopper...")
                            threading.Thread(target=operate_stopper).start() 
                            while (GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW or GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW or GPIO.input(IR_LARGE_PIN) == GPIO.LOW):
                                time.sleep(0.01) 
                            time.sleep(TIME_TO_MEDIUM)
                            print("2. Opening Medium Gate...")
                            medium_gate.angle = GATE_OPEN
                            time.sleep(MEDIUM_DROP_TIME) 
                            print("3. Closing Medium Gate...")
                            medium_gate.angle = GATE_CLOSED
                            
                        elif detected_size == "LARGE":
                            print("1. Releasing Stopper...")
                            threading.Thread(target=operate_stopper).start() 
                            while (GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW or GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW or GPIO.input(IR_LARGE_PIN) == GPIO.LOW):
                                time.sleep(0.01) 
                            time.sleep(TIME_TO_LARGE)
                            print("2. Opening Large Gate...")
                            large_gate.angle = GATE_OPEN
                            time.sleep(LARGE_DROP_TIME) 
                            print("3. Closing Large Gate...")
                            large_gate.angle = GATE_CLOSED
                    
                    print("⏳ Ready for the next mango.")
                    time.sleep(0.5) 
                
                time.sleep(0.01)
            
            # STOP REQUESTED
            print("\n🛑 Stop signal received! Shutting down hardware...")
            hopper_active = False
            stop_conveyor()
            lock_gates()
            print("Hardware safely powered down.")
            
        else:
            # WAITING FOR START SIGNAL
            time.sleep(0.5)

except KeyboardInterrupt:
    print("\n🛑 Ctrl+C Detected! Commencing safety shutdown...")

finally:
    print("Powering down all hardware...")
    hopper_active = False 
    stop_conveyor()
    rotating_gate.throttle = 0.0 
    lock_gates()
    time.sleep(0.5) 
    pca.deinit()
    GPIO.cleanup()
    print("✅ Hardware safely powered down. Program exited.")
