from flask import Flask, jsonify, request
from flask_cors import CORS
import RPi.GPIO as GPIO
import time
import board
import busio
import threading
import subprocess
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from ultralytics import YOLO

# ==========================================
# 1. SETUP HARDWARE & AI
# ==========================================
print("Initializing Hardware...")
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Load YOLO Model ---
print("Loading AI Brain (final weights.pt)...")
model = YOLO("final weights.pt")

# ⚠️ IMPORTANT: Change this to match the EXACT class name from your Roboflow dataset
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

# Travel Times
TIME_TO_MEDIUM = 0.8
TIME_TO_LARGE = 2.2

# Drop Times
SMALL_DROP_TIME = 2.0
MEDIUM_DROP_TIME = 2.3
LARGE_DROP_TIME = 3.5

# Stopper Time
STOPPER_DELAY = 1.5

# ==========================================
# 3. BACKGROUND THREADS & AI FUNCTION
# ==========================================
print("Starting Conveyor Belt (RIGHT / FORWARD at 75% Speed)...")
GPIO.output(R_EN, GPIO.HIGH)
GPIO.output(L_EN, GPIO.HIGH)
GPIO.output(LPWM, GPIO.LOW)

conveyor_pwm.ChangeDutyCycle(100)
time.sleep(0.2)
conveyor_pwm.ChangeDutyCycle(75)

hopper_active = True

def pulse_hopper():
    while hopper_active:
        rotating_gate.throttle = -0.15
        for _ in range(20):
            if not hopper_active:
                return
            time.sleep(0.1)
        rotating_gate.throttle = 0.0
        for _ in range(30):
            if not hopper_active:
                return
            time.sleep(0.1)


print("Starting Background Thread for Hopper Gate...")
hopper_thread = threading.Thread(target=pulse_hopper)
hopper_thread.daemon = True
hopper_thread.start()


def operate_stopper():
    """Opens the stopper and strictly closes it after STOPPER_DELAY."""
    barrier_gate.angle = BARRIER_RELEASED
    time.sleep(STOPPER_DELAY)
    barrier_gate.angle = BARRIER_LOCKED
    print("   [Stopper Gate safely locked behind mango]")


# Shared variable to hold AI results between threads
ai_memory = {"is_defective": False, "finished": False}


def run_ai_check():
    """Runs in the background: Takes a photo and uses YOLO to check for defects."""
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


# ==========================================
# 4. MAIN AUTONOMOUS SENSOR LOOP
# ==========================================
# Global control variables for autonomous loop
sorting_active = False
sorting_paused = False

def autonomous_loop():
    global sorting_active, sorting_paused
    print("\n" + "="*45)
    print("     AUTONOMOUS SORTING & AI ACTIVE")
    print("="*45)
    print("Waiting for mango at the Trigger Sensor...")
    print("Press Ctrl+C to cleanly shut down motors.")
    print("="*45)

    try:
        while True:
            # Check if sorting is active
            if not sorting_active:
                time.sleep(0.1)
                continue

            # Check if sorting is paused
            if sorting_paused:
                time.sleep(0.1)
                continue

            if GPIO.input(IR_TRIGGER_PIN) == GPIO.LOW:
                print("\n🥭 MANGO DETECTED IN CHAMBER!")
                time.sleep(0.2)  # Let it physically settle

                # 1. Fire off the AI in the background
                ai_memory["finished"] = False
                threading.Thread(target=run_ai_check).start()

                # 2. Continuously scan IR sensors while the AI thinks
                detected_size = "SMALL"
                end_time = time.time() + SCAN_DELAY

                print(
                    f"   📐 Actively tracking physical size for {SCAN_DELAY} seconds...")
                while time.time() < end_time:
                    if GPIO.input(IR_LARGE_PIN) == GPIO.LOW:
                        detected_size = "LARGE"
                    elif GPIO.input(IR_MEDIUM_PIN) == GPIO.LOW and detected_size != "LARGE":
                        detected_size = "MEDIUM"
                    time.sleep(0.01)

                # 3. Failsafe: Wait just in case the AI took longer than 3 seconds
                while not ai_memory["finished"]:
                    time.sleep(0.01)

                # 4. Routing Logic
                if ai_memory["is_defective"]:
                    print(
                        "🚫 DEFECTIVE MANGO DETECTED! Bypassing all sorting gates.")
                    print("1. Releasing Stopper to clear the chamber...")
                    threading.Thread(target=operate_stopper).start()

                    # ⬆️ THE FIX: Force the Pi to wait until the bad mango physically leaves the chamber
                    print(
                        "   Waiting for defective mango to physically clear the sensors...")
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

    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C Detected! Commencing safety shutdown...")

    finally:
        print("Powering down Conveyor and Hopper...")
        hopper_active = False
        conveyor_pwm.stop()
        GPIO.output(RPWM, GPIO.LOW)
        GPIO.output(LPWM, GPIO.LOW)
        GPIO.output(R_EN, GPIO.LOW)
        GPIO.output(L_EN, GPIO.LOW)
        rotating_gate.throttle = 0.0

        print("Locking sorting gates...")
        barrier_gate.angle = BARRIER_LOCKED
        small_gate.angle = GATE_CLOSED
        medium_gate.angle = GATE_CLOSED
        large_gate.angle = GATE_CLOSED
        time.sleep(0.5)
        pca.deinit()
        GPIO.cleanup()
        print("✅ Hardware safely powered down. Program exited.")

# ==========================================
# 5. FLASK API
# ==========================================
app = Flask(__name__)
CORS(app)

@app.route('/gate', methods=['POST'])
def control_gate():
    data = request.get_json()
    gate = data.get('gate')
    action = data.get('action')

    if gate == 'barrier':
        barrier_gate.angle = BARRIER_RELEASED if action == 'open' else BARRIER_LOCKED
    elif gate == 'small':
        small_gate.angle = GATE_OPEN if action == 'open' else GATE_CLOSED
    elif gate == 'medium':
        medium_gate.angle = GATE_OPEN if action == 'open' else GATE_CLOSED
    elif gate == 'large':
        large_gate.angle = GATE_OPEN if action == 'open' else GATE_CLOSED
    else:
        return jsonify({'success': False, 'message': 'Invalid gate'}), 400

    return jsonify({'success': True, 'message': f'Gate {gate} {action} command sent'})


@app.route('/conveyor', methods=['POST'])
def control_conveyor():
    data = request.get_json()
    action = data.get('action')

    if action == 'start':
        conveyor_pwm.ChangeDutyCycle(75)
    elif action == 'stop':
        conveyor_pwm.ChangeDutyCycle(0)
    else:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400

    return jsonify({'success': True, 'message': f'Conveyor {action} command sent'})


@app.route('/control', methods=['POST'])
def control_sorting():
    global sorting_active, sorting_paused
    data = request.get_json()
    action = data.get('action')

    if action == 'start':
        sorting_active = True
        sorting_paused = False
        conveyor_pwm.ChangeDutyCycle(75)  # Start conveyor
        return jsonify({'success': True, 'message': 'Sorting started'})
    elif action == 'stop':
        sorting_active = False
        sorting_paused = False
        conveyor_pwm.ChangeDutyCycle(0)   # Stop conveyor
        return jsonify({'success': True, 'message': 'Sorting stopped'})
    elif action == 'pause':
        sorting_paused = True
        conveyor_pwm.ChangeDutyCycle(0)   # Stop conveyor
        return jsonify({'success': True, 'message': 'Sorting paused'})
    elif action == 'continue':
        sorting_paused = False
        conveyor_pwm.ChangeDutyCycle(75)  # Start conveyor
        return jsonify({'success': True, 'message': 'Sorting continued'})
    else:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400


@app.route('/status', methods=['GET'])
def get_status():
    global sorting_active, sorting_paused
    return jsonify({
        'sorting_active': sorting_active,
        'sorting_paused': sorting_paused,
        'conveyor_speed': conveyor_pwm.GetDutyCycle() if hasattr(conveyor_pwm, 'GetDutyCycle') else 0
    })


if __name__ == '__main__':
    try:
        # Start the autonomous loop in a separate thread
        autonomous_thread = threading.Thread(target=autonomous_loop)
        autonomous_thread.daemon = True
        autonomous_thread.start()
        # Run the Flask app in the main thread
        app.run(host='0.0.0.0', port=5001)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        GPIO.cleanup()

