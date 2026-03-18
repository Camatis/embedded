#!/usr/bin/env python3
"""temp_monitor.py

Run on Raspberry Pi to expose CPU temperature to the web app.
Usage:
  pip install flask
  python temp_monitor.py

Then call: http://<rpi-ip>:5800/temp

"""

from flask import Flask, jsonify
import subprocess
import os

app = Flask(__name__)


def read_temp():
    """Return CPU temperature in Celsius for Raspberry Pi."""
    # Try vcgencmd first (Raspberry Pi command)
    try:
        output = subprocess.check_output(['vcgencmd', 'measure_temp'], stderr=subprocess.STDOUT, timeout=5).decode('utf-8').strip()
        if output:
            # e.g. temp=48.3'C
            parts = output.replace("'C", '').split('=')
            if len(parts) == 2:
                return float(parts[1])
    except Exception:
        pass

    # Fallback to thermal-zone path
    try:
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                raw = f.read().strip()
                return float(raw) / 1000.0
    except Exception:
        pass

    raise RuntimeError('Cannot read CPU temperature')


@app.route('/temp', methods=['GET'])
def get_temp():
    try:
        value = read_temp()
        return jsonify({'success': True, 'cpuTemp': round(value, 1), 'unit': 'C'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/', methods=['GET'])
def root():
    return jsonify({'message': 'RPi temp_monitor running', 'endpoint': '/temp'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5800, debug=False)
