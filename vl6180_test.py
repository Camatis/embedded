#!/usr/bin/env python3
"""
VL6180 ToF Sensor Test Suite
Tests three VL6180X sensors on different I2C addresses for:
- Sensor initialization
- Distance readings
- Continuous monitoring
- Address validation
"""

import time
import board
import busio
from adafruit_vl6180x import VL6180X

# ==========================================
# VL6180 SENSOR CONFIGURATION
# ==========================================
# VL6180X sensors can be on different I2C addresses via SHUTDOWN pins
# Default I2C address: 0x29
# You can have up to 3 sensors on the same I2C bus by controlling XSHUT pins

# I2C Configuration
I2C = busio.I2C(board.SCL, board.SDA)

# Sensor I2C Addresses (configure based on your hardware setup)
# If all sensors use address 0x29, use GPIO pins to control XSHUT for sequencing
TRIGGER_SENSOR_ADDR = 0x29   # Main trigger sensor (always enabled)
MEDIUM_SENSOR_ADDR = 0x29    # Medium size sensor (enable via GPIO)
LARGE_SENSOR_ADDR = 0x29     # Large size sensor (enable via GPIO)

# XSHUT GPIO Pins (LOW = sensor off, HIGH = sensor on)
# If not using GPIO control, comment these out
TRIGGER_XSHUT = None  # Leave enabled
MEDIUM_XSHUT = None
LARGE_XSHUT = None

# Distance Thresholds (in mm)
# Adjust these based on your physical setup and mango sizes
TRIGGER_THRESHOLD = 60        # Mango detected if distance < 60mm
MEDIUM_THRESHOLD = 80         # Medium mango: 60-80mm
LARGE_THRESHOLD = 100         # Large mango: >80mm
SMALL_MAX = 60                # Small mango: <60mm

# ==========================================
# SENSOR INITIALIZATION
# ==========================================
def initialize_sensors():
    """Initialize VL6180X sensors and validate communication."""
    sensors = {}
    
    print("Initializing VL6180X sensors...")
    print("=" * 60)
    
    try:
        print("\n🔍 Attempting to detect VL6180X sensor(s) on I2C bus...")
        print(f"   I2C Frequency: {I2C.frequency} Hz")
        
        # Scan I2C bus for devices
        i2c_devices = I2C.scan()
        print(f"   Found {len(i2c_devices)} device(s) on I2C bus: {[hex(d) for d in i2c_devices]}")
        
        # Check if VL6180X (0x29) is present
        if 0x29 in i2c_devices:
            print("   ✓ VL6180X detected at address 0x29")
        else:
            print("   ✗ VL6180X NOT found at address 0x29")
            print("   Troubleshooting:")
            print("     - Check I2C wiring (SCL/SDA to GPIO 2/3 on RPi)")
            print("     - Verify pull-up resistors (4.7kΩ)")
            print("     - Check XSHUT pin is HIGH (not pulled LOW)")
            print("     - Run: i2cdetect -y 1  (for RPi I2C-1)")
            return sensors
        
        # Initialize trigger sensor
        print("\n📍 Initializing TRIGGER sensor...")
        try:
            sensors['trigger'] = VL6180X(I2C, address=TRIGGER_SENSOR_ADDR)
            print(f"   ✓ Trigger sensor ready at 0x{TRIGGER_SENSOR_ADDR:02x}")
        except Exception as e:
            print(f"   ✗ Failed to initialize trigger sensor: {e}")
        
        # Initialize medium sensor
        print("\n📍 Initializing MEDIUM sensor...")
        try:
            sensors['medium'] = VL6180X(I2C, address=MEDIUM_SENSOR_ADDR)
            print(f"   ✓ Medium sensor ready at 0x{MEDIUM_SENSOR_ADDR:02x}")
        except Exception as e:
            print(f"   ✗ Failed to initialize medium sensor: {e}")
        
        # Initialize large sensor
        print("\n📍 Initializing LARGE sensor...")
        try:
            sensors['large'] = VL6180X(I2C, address=LARGE_SENSOR_ADDR)
            print(f"   ✓ Large sensor ready at 0x{LARGE_SENSOR_ADDR:02x}")
        except Exception as e:
            print(f"   ✗ Failed to initialize large sensor: {e}")
        
        print("\n" + "=" * 60)
        return sensors
        
    except Exception as e:
        print(f"\n✗ I2C initialization error: {e}")
        print("Troubleshooting:")
        print("  - Ensure I2C is enabled: sudo raspi-config → Interfacing Options → I2C")
        print("  - Check RPi I2C version: ls /dev/i2c-*")
        return sensors

# ==========================================
# DISTANCE READING FUNCTIONS
# ==========================================
def read_sensor(sensor, name, attempt=1):
    """
    Read distance from a single VL6180X sensor.
    Returns distance in mm, or None if read fails.
    """
    try:
        distance = sensor.range
        return distance
    except Exception as e:
        print(f"   ✗ {name} read failed (attempt {attempt}): {e}")
        return None

def classify_mango_size(trigger_dist, medium_dist, large_dist):
    """
    Classify mango size based on sensor distances.
    Returns: ("SMALL" | "MEDIUM" | "LARGE" | "UNKNOWN", confidence)
    """
    # Simple classification logic based on thresholds
    # Adjust based on your physical setup
    
    distances = {
        'trigger': trigger_dist if trigger_dist else 999,
        'medium': medium_dist if medium_dist else 999,
        'large': large_dist if large_dist else 999
    }
    
    avg_distance = sum([d for d in distances.values() if d < 999]) / len([d for d in distances.values() if d < 999])
    
    if avg_distance < SMALL_MAX:
        return "SMALL", avg_distance
    elif avg_distance < MEDIUM_THRESHOLD:
        return "MEDIUM", avg_distance
    else:
        return "LARGE", avg_distance

# ==========================================
# TEST MODES
# ==========================================
def test_single_sensor(sensors, sensor_name):
    """Test a single sensor with continuous distance readings."""
    if sensor_name not in sensors:
        print(f"✗ Sensor '{sensor_name}' not initialized")
        return
    
    sensor = sensors[sensor_name]
    print(f"\n🧪 Testing {sensor_name.upper()} sensor...")
    print(f"{'Time (s)':<10} {'Distance (mm)':<15} {'Status':<20}")
    print("-" * 45)
    
    try:
        start_time = time.time()
        for i in range(20):  # 20 readings
            elapsed = time.time() - start_time
            distance = read_sensor(sensor, sensor_name)
            
            if distance is not None:
                # Provide feedback based on distance
                if distance < 30:
                    status = "🔴 VERY CLOSE"
                elif distance < TRIGGER_THRESHOLD:
                    status = "🟡 OBJECT DETECTED"
                elif distance < 150:
                    status = "🟢 OK (out of range)"
                else:
                    status = "⚪ TOO FAR"
                
                print(f"{elapsed:<10.2f} {distance:<15} {status}")
            else:
                print(f"{elapsed:<10.2f} {'ERROR':<15} {'Read failed':<20}")
            
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n📊 Test interrupted by user")
    except Exception as e:
        print(f"❌ Test error: {e}")

def test_all_sensors(sensors):
    """Test all three sensors simultaneously."""
    if not sensors:
        print("✗ No sensors initialized")
        return
    
    print(f"\n🧪 Testing ALL sensors simultaneously...")
    print(f"{'Time':<10} {'Trigger':<12} {'Medium':<12} {'Large':<12} {'Size Prediction':<20}")
    print("-" * 70)
    
    try:
        start_time = time.time()
        for i in range(30):  # 30 readings
            elapsed = time.time() - start_time
            
            readings = {}
            for name, sensor in sensors.items():
                dist = read_sensor(sensor, name)
                readings[name] = dist if dist else 999
            
            # Classify size
            size, confidence = classify_mango_size(
                readings.get('trigger'),
                readings.get('medium'),
                readings.get('large')
            )
            
            trigger_str = f"{readings['trigger']}mm" if readings['trigger'] < 999 else "---"
            medium_str = f"{readings['medium']}mm" if readings['medium'] < 999 else "---"
            large_str = f"{readings['large']}mm" if readings['large'] < 999 else "---"
            
            prediction = f"{size} ({confidence:.0f}mm avg)"
            
            print(f"{elapsed:<10.2f} {trigger_str:<12} {medium_str:<12} {large_str:<12} {prediction:<20}")
            
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n📊 Test interrupted by user")
    except Exception as e:
        print(f"❌ Test error: {e}")

def test_distance_thresholds(sensors):
    """Test sensor response to different distances and thresholds."""
    print(f"\n🧪 Distance Threshold Test")
    print("Place objects at different distances and observe sensor readings")
    print(f"\nConfigured thresholds:")
    print(f"  - Trigger Threshold: {TRIGGER_THRESHOLD}mm (object detected)")
    print(f"  - Small Max: {SMALL_MAX}mm")
    print(f"  - Medium Threshold: {MEDIUM_THRESHOLD}mm")
    print(f"  - Large Threshold: {LARGE_THRESHOLD}mm+")
    print("\nStart placing objects at various distances...")
    print(f"{'Time':<10} {'Trigger':<15} {'State':<20}")
    print("-" * 45)
    
    try:
        start_time = time.time()
        for i in range(50):
            elapsed = time.time() - start_time
            distance = read_sensor(sensors['trigger'], 'trigger')
            
            if distance is not None:
                if distance < TRIGGER_THRESHOLD:
                    state = f"🔴 DETECTED ({distance}mm)"
                else:
                    state = f"⚪ CLEAR ({distance}mm)"
                print(f"{elapsed:<10.2f} {distance:<15} {state:<20}")
            else:
                print(f"{elapsed:<10.2f} {'ERROR':<15} {'Sensor error':<20}")
            
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n📊 Test interrupted by user")
    except Exception as e:
        print(f"❌ Test error: {e}")

def show_menu():
    """Display test menu."""
    print("\n" + "=" * 60)
    print("VL6180 SENSOR TEST SUITE")
    print("=" * 60)
    print("\nSelect a test mode:")
    print("  1. Test TRIGGER sensor only")
    print("  2. Test MEDIUM sensor only")
    print("  3. Test LARGE sensor only")
    print("  4. Test ALL sensors simultaneously")
    print("  5. Test distance thresholds (interactive)")
    print("  6. Exit")
    print("\n" + "-" * 60)

# ==========================================
# MAIN TEST EXECUTION
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("VL6180X ToF SENSOR DIAGNOSTIC TOOL")
    print("=" * 60)
    
    # Initialize sensors
    sensors = initialize_sensors()
    
    if not sensors:
        print("\n❌ No sensors initialized. Cannot proceed with tests.")
        print("\nPlease check:")
        print("  1. I2C wiring is correct (SCL → GPIO 3, SDA → GPIO 2 on RPi)")
        print("  2. Pull-up resistors (4.7kΩ) are installed")
        print("  3. XSHUT pin is HIGH (not pulled LOW)")
        print("  4. Power supply is connected (VDD = 2.7-3.6V)")
        print("  5. I2C is enabled: sudo raspi-config → Interfacing → I2C")
        exit(1)
    
    # Test menu loop
    while True:
        show_menu()
        choice = input("Enter choice (1-6): ").strip()
        
        try:
            if choice == '1':
                test_single_sensor(sensors, 'trigger')
            elif choice == '2':
                test_single_sensor(sensors, 'medium')
            elif choice == '3':
                test_single_sensor(sensors, 'large')
            elif choice == '4':
                test_all_sensors(sensors)
            elif choice == '5':
                test_distance_thresholds(sensors)
            elif choice == '6':
                print("\n✅ Exiting test suite...")
                break
            else:
                print("❌ Invalid choice. Please enter 1-6.")
        except Exception as e:
            print(f"❌ Test error: {e}")
            import traceback
            traceback.print_exc()
    
    print("Test suite closed.")
