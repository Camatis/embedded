import RPi.GPIO as GPIO
import time
import cv2
from ultralytics import YOLO
import signal
import sys

# ==========================================
# GRACEFUL SHUTDOWN SETUP
# ==========================================
def signal_handler(sig, frame):
    print(f'\n📋 {signal.Signals(sig).name} received, initiating graceful shutdown...')
    cleanup_hardware()
    sys.exit(0)

def cleanup_hardware():
    """Safely stop all hardware operations."""
    print('🛑 Stopping all hardware...')
    try:
        # Stop PWM gracefully
        if 'hopper_pwm' in globals():
            hopper_pwm.stop()
            print('✅ Hopper servo stopped')
        
        # Close camera
        if 'cap' in globals():
            cap.release()
            print('✅ Camera released')
        
        # Clean up GPIO
        GPIO.cleanup()
        print('✅ GPIO cleaned up')
    except Exception as e:
        print(f'⚠️  Error during cleanup: {e}')

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==========================================
# HARDWARE SETUP
# ==========================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- 1. Sensors ---
IR_BOTTOM = 17  # Main Trigger at Stopper Gate
GPIO.setup(IR_BOTTOM, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- 2. Hopper Gate (360-Degree Servo) ---
HOPPER_PIN = 12 
GPIO.setup(HOPPER_PIN, GPIO.OUT)
hopper_pwm = GPIO.PWM(HOPPER_PIN, 50)
hopper_pwm.start(0) 

# --- 3. Stopper Gate (A4988 Stepper) ---
STOPPER_DIR = 23
STOPPER_STEP = 24
GPIO.setup(STOPPER_DIR, GPIO.OUT)
GPIO.setup(STOPPER_STEP, GPIO.OUT)

# --- 4. Sorting Gates (A4988 Steppers) ---
GATES = {
    "SMALL": (5, 6),
    "MEDIUM": (13, 19),
    "LARGE": (26, 21)
}
for size, pins in GATES.items():
    GPIO.setup(pins[0], GPIO.OUT) # DIR Pin
    GPIO.setup(pins[1], GPIO.OUT) # STEP Pin

# --- 5. AI Vision ---
print("Loading YOLO Model...")
mango_model = YOLO('final_weights.pt')
cap = cv2.VideoCapture(0)


# ==========================================
# MOVEMENT FUNCTIONS
# ==========================================

def simultaneous_load_and_release():
    """
    Opens the stopper gate for Mango #1 while simultaneously 
    rotating the hopper to drop Mango #2.
    """
    steps_for_90 = 50
    step_delay = 0.005 # 50 steps * 0.01s total loop time = 0.5 seconds
    
    print("🔄 PIPELINE: Hopper Dropping & Stopper Opening...")
    
    # 1. Start Hopper Servo (Runs in background)
    hopper_pwm.ChangeDutyCycle(8.5) 
    
    # 2. Lift Stopper Gate (Traps Python for ~0.5 seconds)
    GPIO.output(STOPPER_DIR, GPIO.HIGH) 
    for _ in range(steps_for_90):
        GPIO.output(STOPPER_STEP, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(STOPPER_STEP, GPIO.LOW)
        time.sleep(step_delay)
        
    # 3. Stop Hopper Servo (0.5s timer reached)
    hopper_pwm.ChangeDutyCycle(0) 
    
    # 4. Wait for Mango #1 to roll out of the chamber
    time.sleep(1.5) 
    
    # 5. Close Stopper Gate (Catching Mango #2)
    print("🚧 Stopper Gate DOWN.")
    GPIO.output(STOPPER_DIR, GPIO.LOW) 
    for _ in range(steps_for_90):
        GPIO.output(STOPPER_STEP, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(STOPPER_STEP, GPIO.LOW)
        time.sleep(step_delay)

def rotate_sorting_gate(size_category):
    """
    Swings the selected sorting gate 70 degrees open, waits, and snaps shut.
    """
    if size_category not in GATES:
        return
        
    dir_pin, step_pin = GATES[size_category]
    steps_for_70_deg = 39 
    step_delay = 0.005 
    
    print(f"⚙️ Opening {size_category} sorting gate...")
    
    # Swing Open
    GPIO.output(dir_pin, GPIO.HIGH)
    for _ in range(steps_for_70_deg):
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(step_delay)
        
    # Wait for the mango to fall down the chute
    time.sleep(3.0) 
    
    # Swing Closed
    GPIO.output(dir_pin, GPIO.LOW)
    for _ in range(steps_for_70_deg):
        GPIO.output(step_pin, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(step_pin, GPIO.LOW)
        time.sleep(step_delay)
        
    print(f"✅ {size_category} gate locked.")

def capture_and_grade_mango():
    """Takes photos and grades the mango."""
    print("📸 Scanning Carabao Mango...")
    time.sleep(1.5) # Simulated grading
    return "GOOD", "MEDIUM"


# ==========================================
# MAIN MACHINE LOOP
# ==========================================

print("✅ Powering on. Beginning Startup Sequence...")

# Initial Startup Drop
hopper_pwm.ChangeDutyCycle(8.5)
time.sleep(0.45)
hopper_pwm.ChangeDutyCycle(0)

try:
    while True:
        # Wait for mango to hit the stopper
        if GPIO.input(IR_BOTTOM) == GPIO.LOW:
            print("\n🥭 Mango ready in scanning chamber!")
            
            # 1. Scan
            health, size = capture_and_grade_mango()
            print(f"--- RESULTS: {health} | {size} ---")
            
            # 2. Synchronous Pipeline Move
            simultaneous_load_and_release()
            
            # 3. Conveyor Travel Time
            # The mango needs time to travel from the stopper to the sorting gates.
            # Adjust this based on your 0.5cm/s conveyor speed!
            print(f"⏳ Waiting for mango to travel down the belt...")
            time.sleep(2.0) 
            
            # 4. Route the Mango
            if health == "DEFECTIVE":
                print("🚨 Defective! Bypassing gates to the reject bin.")
            else:
                rotate_sorting_gate(size)
            
        time.sleep(0.05)

except KeyboardInterrupt:
    print("\n🛑 Machine stopped by user.")
finally:
    cleanup_hardware()
    cap.release()

