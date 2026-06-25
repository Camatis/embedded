import RPi.GPIO as GPIO
import time

# --- GPIO Pin Definitions ---
PIN_GREEN_LED = 5
PIN_RED_LED = 6
PIN_BUZZER = 16

# --- System State Variables ---
ui_popup_active = False  
machine_state = "READY"  

# ==========================================
# MISSING MOTOR PLACEHOLDERS (Add your real I2C/Motor code here later)
# ==========================================
def stop_conveyor():
    print("[HARDWARE] Conveyor Stopped")

def start_conveyor():
    print("[HARDWARE] Conveyor Started")

def reverse_conveyor():
    print("[HARDWARE] Conveyor Reversed")

def reset_servos_to_default():
    print("[HARDWARE] Servos reset to default closed positions")

# ==========================================
# YOUR EXISTING LOGIC
# ==========================================
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(PIN_GREEN_LED, GPIO.OUT)
    GPIO.setup(PIN_RED_LED, GPIO.OUT)
    GPIO.setup(PIN_BUZZER, GPIO.OUT)
    set_machine_ready()
    print("✓ GPIO Setup Complete. Machine is READY.")

def set_machine_ready():
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.LOW)

def set_machine_scanning():
    GPIO.output(PIN_GREEN_LED, GPIO.LOW)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)

def handle_multi_mango_error():
    global machine_state, ui_popup_active
    print("⚠ ERROR: MULTIPLE MANGOES DETECTED!")
    machine_state = "PAUSED"
    stop_conveyor()                 
    reset_servos_to_default()       
    
    # Trigger buzzer
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(1.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    # Simulate waiting for UI to clear
    print("Waiting for operator to clear UI popup...")
    time.sleep(3) # Placeholder for UI wait
        
    print("UI Cleared. Resuming...")
    start_conveyor()
    machine_state = "READY"

def execute_pause():
    global machine_state
    machine_state = "PAUSED"
    stop_conveyor()
    print("⏸ System Paused. Current batch counts retained in RAM.")

def execute_stop():
    global machine_state
    machine_state = "STOPPED"
    stop_conveyor()
    reset_servos_to_default()
    set_machine_ready() 
    print("🛑 System Stopped. Hardware returned to safe defaults.")

def execute_rejection(reason):
    print(f"🚫 REJECTION TRIGGERED: {reason}")
    stop_conveyor()
    
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(0.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    reverse_conveyor()              
    time.sleep(2.0)                 
    stop_conveyor()
    
    set_machine_ready()

# ==========================================
# THE EXECUTION LOOP (This makes it run!)
# ==========================================
if __name__ == '__main__':
    try:
        # 1. Initialize everything
        setup_gpio()
        
        # 2. Keep the script alive
        print("\n--- MangPain Hardware Loop Started ---")
        print("Press CTRL+C to safely exit and cleanup GPIO.")
        
        while True:
            # For now, it just loops safely. 
            # Later, your ZMQ subscriber or Flask API will trigger the functions here.
            time.sleep(1)
            
    except KeyboardInterrupt:
        # 3. Clean up safely when you stop the script
        print("\nForce quitting... Cleaning up GPIO pins.")
        GPIO.cleanup()