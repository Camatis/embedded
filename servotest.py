import RPi.GPIO as GPIO
import time

# --- GPIO Pin Definitions ---
PIN_GREEN_LED = 5
PIN_RED_LED = 6
PIN_BUZZER = 16

# --- System State Variables ---
ui_popup_active = False  # This should be updated via your Express API polling
machine_state = "READY"  # States: READY, SCANNING, PAUSED, STOPPED

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(PIN_GREEN_LED, GPIO.OUT)
    GPIO.setup(PIN_RED_LED, GPIO.OUT)
    GPIO.setup(PIN_BUZZER, GPIO.OUT)
    set_machine_ready()

# --- Task 1: Red and Green Lights ---
def set_machine_ready():
    """Green LED active, Red LED off. Ready for new mango."""
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.LOW)

def set_machine_scanning():
    """Red LED active, Green LED off. Mango in transit/scanning."""
    GPIO.output(PIN_GREEN_LED, GPIO.LOW)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)

# --- Task 2: Buzzer & Multi-Mango Detection ---
def handle_multi_mango_error():
    """Triggered when YOLO/Sensors detect 2+ mangoes."""
    global machine_state, ui_popup_active
    
    machine_state = "PAUSED"
    stop_conveyor()                 # Call your motor stop function
    reset_servos_to_default()       # Call your PCA9685 reset function
    
    # Trigger buzzer
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(1.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    # Wait until React UI confirms the popup is closed
    while ui_popup_active:
        time.sleep(0.5) 
        # In reality, this flag updates via a ZMQ message or API poll from React
        
    start_conveyor()
    machine_state = "READY"

# --- Task 3 & 4: Pause and Stop Logic ---
def execute_pause():
    """Immediately halts physical movement without finalizing data."""
    global machine_state
    machine_state = "PAUSED"
    stop_conveyor()
    print("System Paused. Current batch counts retained in RAM.")

def execute_stop():
    """Terminates process, safes hardware, and preps for shutdown/new batch."""
    global machine_state
    machine_state = "STOPPED"
    stop_conveyor()
    reset_servos_to_default()
    set_machine_ready() # Reset lights
    print("System Stopped. Hardware returned to safe defaults.")

# --- Task 5 & 6: Rejection Routines (Non-Organic / Not Carabao) ---
def execute_rejection(reason):
    """Handles items that fail the variety or organic checks."""
    # reason can be "NON_ORGANIC" or "NOT_CARABAO"
    
    stop_conveyor()
    
    # Trigger Buzzer alert
    GPIO.output(PIN_BUZZER, GPIO.HIGH)
    time.sleep(0.5)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    
    # Reverse conveyor to eject the item
    reverse_conveyor()              
    time.sleep(2.0)                 # Run reverse for 2 seconds
    stop_conveyor()
    
    # Note: The UI pop-up ("No Carabao Mangoes Detected") must be triggered 
    # by sending a JSON flag through your API gateway to the React frontend.
    set_machine_ready()