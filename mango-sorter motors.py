import RPi.GPIO as GPIO
import time
import threading
import requests

# ==========================================
# 1. HARDWARE CONFIGURATION
# ==========================================

# --- IR SENSORS (Size Grading) ---
# Logic: LOW (0) when blocked
PIN_IR_TOP = 17   # Large Sensor
PIN_IR_MID = 27   # Medium Sensor
PIN_IR_BOT = 22   # Small Sensor & MAIN TRIGGER

# --- CONVEYOR MOTOR [Step, Dir, En] ---
CONVEYOR = [23, 24, 25]

# --- SORTING GATE MOTORS [Step, Dir, En] ---
GATE_SMALL  = [5, 12, 7]    # Bin 1 (Closest)
GATE_MEDIUM = [6, 13, 8]    # Bin 2 (Middle)
GATE_LARGE  = [19, 26, 11]  # Bin 3 (Farthest)

# --- TIMING & SPEED SETTINGS ---
# Time for mango to travel from Camera/Sensor to the Gate
TRAVEL_TIME_TO_SMALL  = 5.0   
TRAVEL_TIME_TO_MEDIUM = 10.0  
TRAVEL_TIME_TO_LARGE  = 15.0  

SORTER_SLEEP = 0.02   # Speed of gate arm
STEPS_FOR_70_DEG = 39 # Steps to open gate
CONVEYOR_SLEEP = 0.02 # Conveyor speed

# --- GLOBAL VARIABLES ---
running = True

# --- WEB APP INTEGRATION ---
BACKEND_URL = 'http://localhost:5000/api/sensor-data'

def send_sensor_data(size):
    """Send mango size detection to web app backend."""
    size_map = {'Small': 1, 'Medium': 2, 'Large': 3}
    payload = {
        'small': size == 'Small',
        'medium': size == 'Medium',
        'large': size == 'Large',
        'defective': False,
        'detectedSize': size_map.get(size, None)
    }
    try:
        requests.post(BACKEND_URL, json=payload, timeout=1.0)
        print(f"📤 Sent to web app: {size}")
    except Exception as e:
        print(f"⚠️ Backend connection failed: {e}")

# ==========================================
# 2. MOTOR FUNCTIONS
# ==========================================

def run_conveyor():
    """Background thread to keep conveyor moving constantly."""
    step_pin, dir_pin, en_pin = CONVEYOR
    GPIO.output(dir_pin, 1) # 1 = Forward
    GPIO.output(en_pin, GPIO.LOW) # Enable
    
    print("🏃 Conveyor started...")
    while running:
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(CONVEYOR_SLEEP)
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(CONVEYOR_SLEEP)

def move_gate_gentle(gate_pins, steps, direction):
    """Moves a specific gate arm open or closed."""
    step_pin, dir_pin, en_pin = gate_pins
    
    # REVERSE LOGIC (Right Rotation)
    actual_direction = 1 - direction
    
    GPIO.output(dir_pin, actual_direction)
    GPIO.output(en_pin, GPIO.LOW) # Enable driver
    
    for _ in range(steps):
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(SORTER_SLEEP) 
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(SORTER_SLEEP)
        
    GPIO.output(en_pin, GPIO.HIGH) # Disable driver to save power

# ==========================================
# 3. MAIN LOGIC
# ==========================================

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Setup Sensors
    GPIO.setup([PIN_IR_TOP, PIN_IR_MID, PIN_IR_BOT], GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Setup Motors
    all_motor_pins = CONVEYOR + GATE_SMALL + GATE_MEDIUM + GATE_LARGE
    GPIO.setup(all_motor_pins, GPIO.OUT)
    
    print("✅ Hardware Ready.")

def get_size():
    """Reads IR sensors to determine object size."""
    if GPIO.input(PIN_IR_TOP) == 0:
        return "Large"
    elif GPIO.input(PIN_IR_MID) == 0:
        return "Medium"
    elif GPIO.input(PIN_IR_BOT) == 0:
        return "Small"
    return None

def main():
    global running
    setup_gpio()
    
    # Start Conveyor (Thread)
    conveyor_thread = threading.Thread(target=run_conveyor)
    conveyor_thread.start()

    try:
        print("🔧 TEST MODE: Waiting for IR triggers...")
        while True:
            # 1. TRIGGER: Wait for object at Bottom Sensor
            if GPIO.input(PIN_IR_BOT) == 0:
                print("\n📦 Object Detected! Aligning for 2 seconds...")
                
                # --- NEW LOGIC: WAIT 2 SECONDS BEFORE READING ---
                time.sleep(2.0) 
                
                # 2. SIZE CHECK (Happens after the wait)
                size = get_size()
                print(f"📏 Size Classified: {size}")
                
                # Send to web app
                if size:
                    send_sensor_data(size)
                
                # 3. SORTING LOGIC
                target_gate = None
                wait_time = 0
                
                if size == "Small":
                    target_gate = GATE_SMALL
                    wait_time = TRAVEL_TIME_TO_SMALL
                elif size == "Medium":
                    target_gate = GATE_MEDIUM
                    wait_time = TRAVEL_TIME_TO_MEDIUM
                elif size == "Large":
                    target_gate = GATE_LARGE
                    wait_time = TRAVEL_TIME_TO_LARGE
                
                # 4. EXECUTION
                if target_gate:
                    print(f"🚀 Opening {size} Gate INSTANTLY...")
                    
                    # A. OPEN GATE NOW
                    move_gate_gentle(target_gate, STEPS_FOR_70_DEG, direction=1)
                    
                    # B. WAIT FOR ARRIVAL
                    # We keep the gate open for the travel time + buffer
                    total_wait = wait_time + 2.0 
                    print(f"⏳ Gate Open. Waiting {total_wait}s for mango to arrive...")
                    time.sleep(total_wait)
                    
                    # C. CLOSE GATE
                    move_gate_gentle(target_gate, STEPS_FOR_70_DEG, direction=0)
                    print("✅ Gate Closed.")
                else:
                    print("⚠️ Error: Sensors triggered but size is Undetermined.")

                # 5. RESET
                print("Ready for next object...")
                time.sleep(1) 

    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        running = False
        conveyor_thread.join()
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()


