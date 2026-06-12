VL6180 ToF Sensor - Dependencies & Setup Guide
================================================

## Required Python Packages

### Primary Library (VL6180 Driver)
- **adafruit-circuitpython-vl6180x** - Official Adafruit library for VL6180X sensors
  Installation: pip install adafruit-circuitpython-vl6180x

### I2C Communication
- **adafruit-circuitpython-busdevice** - Low-level I2C/SPI bus device library
  Installation: pip install adafruit-circuitpython-busdevice
  
  Note: Usually installed as a dependency of adafruit-circuitpython-vl6180x

### Existing Dependencies (Already in Your Code)
- board              - CircuitPython hardware definitions (included with RPi OS)
- busio             - I2C/SPI interface (included with CircuitPython on RPi)
- RPi.GPIO          - GPIO control (already used in servotest.py)
- time, threading   - Standard library modules
- adafruit_pca9685, adafruit_motor - Already used for servos

## Complete Installation Commands

### 1. Update System Packages
sudo apt-get update
sudo apt-get upgrade -y

### 2. Enable I2C Interface
sudo raspi-config
# Navigate to: Interfacing Options → I2C → Enable
# Or use: sudo sed -i 's/#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' /boot/config.txt

### 3. Install I2C Tools (for diagnostics)
sudo apt-get install -y i2c-tools python3-dev

### 4. Install Python Package Manager
sudo apt-get install -y python3-pip

### 5. Install Required Python Libraries
pip install adafruit-circuitpython-vl6180x
pip install adafruit-circuitpython-busdevice

### 6. Verify Installation
python3 -c "import board; import busio; from adafruit_vl6180x import VL6180X; print('✓ All imports successful')"

## VL6180 Hardware Setup

### Wiring (I2C Connection)
RPi GPIO Pins → VL6180X Sensor
- GPIO 2 (SDA) → VL6180X SDA
- GPIO 3 (SCL) → VL6180X SCL
- 3.3V → VL6180X VDD
- GND → VL6180X GND

### Important Notes
1. VL6180X operates at 3.3V (NOT 5V!) - RPi is 3.3V safe
2. Pull-up resistors: Install 4.7kΩ resistors on SDA and SCL to 3.3V
   - These are often included on sensor breakout boards
3. XSHUT Pin: Leave HIGH (pulled to 3.3V) for normal operation
   - Can be connected to GPIO for power control if needed
4. Bypass Capacitor: 0.1μF ceramic capacitor recommended between VDD and GND

### Multiple Sensors on Same I2C Bus
- Default address: 0x29
- To add multiple sensors:
  Option A: Use GPIO pins to control XSHUT of each sensor sequentially
  Option B: Use I2C address modification (requires XSHUT control)
  Option C: Use separate I2C buses (RPi has I2C-1 and I2C-0)

## Quick Hardware Diagnostics

### Check I2C Bus
i2cdetect -y 1
# Should show VL6180X at 0x29

### Check GPIO Pins
# Verify I2C pins are accessible:
python3 -c "import board; print(f'SCL: {board.SCL}'); print(f'SDA: {board.SDA}')"

### Test Before Integration
python3 vl6180_test.py
# Run interactive tests to verify sensors are working

## Troubleshooting

### "No module named 'adafruit_vl6180x'"
Solution: pip install adafruit-circuitpython-vl6180x

### "I2C device not found"
Solution:
1. Check wiring (SDA/SCL pins)
2. Verify I2C is enabled: sudo raspi-config → Interfacing → I2C
3. Check pull-up resistors (4.7kΩ on SDA/SCL)
4. Test with: i2cdetect -y 1

### "Permission denied" errors
Solution: Run with sudo or add user to i2c group:
sudo usermod -aG i2c $USER
# Then log out and log back in

### Sensor returns 0 or 65535 (invalid readings)
Solution:
1. Check VDD voltage (should be 3.3V)
2. Verify I2C connection quality
3. Check sensor isn't damaged (test with different sensor if available)
4. Reduce I2C bus speed if interference suspected

## Integration Changes

The servotest.py modifications will:
1. Replace GPIO-based IR sensors with VL6180 distance measurement
2. Use distance thresholds to detect mango presence and size
3. Maintain the same API (still uses GPIO.input() interface internally)
4. Add distance-based diagnostics

Key threshold values to tune (in mm):
- Trigger Threshold: Distance at which mango is detected (~60mm)
- Small Max: Maximum distance for small mango (~60mm)
- Medium Threshold: Distance for medium classification (~80mm)
- Large Threshold: Minimum distance for large mango (~80mm)

Adjust these based on your actual mango sizes and sensor positioning.

## Performance Characteristics

- Measurement Range: 0-100mm (typical for mango detection)
- Accuracy: ±10mm (typical)
- Refresh Rate: ~10-20Hz (can be increased)
- Response Time: ~5-10ms
- I2C Speed: Standard 100kHz (400kHz supported)
- Power Consumption: ~24mA per sensor

## Cost Reference

VL6180X sensors: ~$5-15 each (Adafruit/Sparkfun breakout boards)
Total for 3 sensors: ~$15-45
