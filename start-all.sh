#!/bin/bash
# To Run:  cd embedded && ./start-all.sh
# Stop:    Press Ctrl+C

# Always run from the embedded/ directory regardless of where script is called from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "════════════════════════════════════════════════════"
echo "       Mango Sorter System — Starting Up"
echo "════════════════════════════════════════════════════"
echo ""

# ── Activate virtual environment (inherited by all background processes) ───────
VENV_ACTIVATE="$SCRIPT_DIR/../virtual_env/myenv/bin/activate"
if [ -f "$VENV_ACTIVATE" ]; then
  source "$VENV_ACTIVATE"
  echo "✓ Virtual environment activated: $VIRTUAL_ENV"
else
  echo "⚠  Virtual environment not found at: $VENV_ACTIVATE"
  echo "   Python scripts may fail if packages are not installed globally."
fi

echo ""
echo "Starting services in order..."
echo ""

# ── 1st: webrtc_stream.py  (camera + YOLO detection, port 8082) ───────────────
echo "[1/5] webrtc_stream.py  — Camera stream + YOLO detection"
if [ -f "$SCRIPT_DIR/webrtc_stream.py" ]; then
  python3 "$SCRIPT_DIR/webrtc_stream.py" > /tmp/webrtc_stream.log 2>&1 &
  WEBRTC_PID=$!
  echo "      ✓ PID: $WEBRTC_PID  |  log: /tmp/webrtc_stream.log"
  sleep 5   # allow camera to open and YOLO model to load
else
  echo "      ⚠  webrtc_stream.py not found — skipping"
  WEBRTC_PID=""
fi

# ── 2nd: servotest.py  (hardware controller, port 5000) ───────────────────────
echo "[2/5] servotest.py      — Hardware controller (GPIO / servos / IR)"
if [ -f "$SCRIPT_DIR/servotest.py" ]; then
  python3 "$SCRIPT_DIR/servotest.py" > /tmp/servotest.log 2>&1 &
  SERVOTEST_PID=$!
  echo "      ✓ PID: $SERVOTEST_PID  |  log: /tmp/servotest.log"
  sleep 4   # allow GPIO + PCA9685 + Flask to initialise
else
  echo "      ⚠  servotest.py not found — skipping"
  SERVOTEST_PID=""
fi

# ── 3rd: temp_monitor.py  (CPU temperature monitor) ──────────────────────────
echo "[3/5] temp_monitor.py   — CPU temperature monitor"
if [ -f "$SCRIPT_DIR/temp_monitor.py" ]; then
  python3 "$SCRIPT_DIR/temp_monitor.py" > /tmp/temp_monitor.log 2>&1 &
  TEMP_PID=$!
  echo "      ✓ PID: $TEMP_PID  |  log: /tmp/temp_monitor.log"
  sleep 1
else
  echo "      ⚠  temp_monitor.py not found (optional) — skipping"
  TEMP_PID=""
fi

# ── 4th: Node.js backend  (Express + MongoDB, port 5001) ─────────────────────
echo "[4/5] backend           — Node.js / Express API"
cd "$SCRIPT_DIR/thesis_website/backend"
npm install > /dev/null 2>&1 || true
npm run dev > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "      ✓ PID: $BACKEND_PID  |  log: /tmp/backend.log"
sleep 5   # allow Express to start and MongoDB to connect

# ── 5th: React frontend  (port 3000) ─────────────────────────────────────────
echo "[5/5] frontend          — React app"
cd "$SCRIPT_DIR/thesis_website/frontend"
npm install > /dev/null 2>&1 || true
GENERATE_SOURCEMAP=false npm start > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "      ✓ PID: $FRONTEND_PID  |  log: /tmp/frontend.log"

cd "$SCRIPT_DIR"

echo ""
echo "════════════════════════════════════════════════════"
echo "✓  All services started!"
echo "════════════════════════════════════════════════════"
echo ""
echo "  Access the system:"
echo "    🌐  Frontend (UI)     →  http://raspberrypi.local:3000"
echo "    🔧  Backend API       →  http://localhost:5001"
echo "    📡  WebRTC / YOLO     →  http://localhost:8082"
echo "    🤖  Hardware API      →  http://localhost:5000"
echo ""
echo "  Live logs:"
echo "    tail -f /tmp/webrtc_stream.log"
echo "    tail -f /tmp/servotest.log"
echo "    tail -f /tmp/temp_monitor.log"
echo "    tail -f /tmp/backend.log"
echo "    tail -f /tmp/frontend.log"
echo ""
echo "  Press Ctrl+C to stop everything."
echo "════════════════════════════════════════════════════"
echo ""

# ── Graceful shutdown when Ctrl+C is pressed ──────────────────────────────────
trap '
  echo ""
  echo "Shutting down all services..."
  [ -n "$FRONTEND_PID" ]  && kill "$FRONTEND_PID"  2>/dev/null
  [ -n "$BACKEND_PID" ]   && kill "$BACKEND_PID"   2>/dev/null
  [ -n "$TEMP_PID" ]      && kill "$TEMP_PID"      2>/dev/null
  [ -n "$SERVOTEST_PID" ] && kill "$SERVOTEST_PID" 2>/dev/null
  [ -n "$WEBRTC_PID" ]    && kill "$WEBRTC_PID"    2>/dev/null
  echo "All services stopped."
  exit 0
' INT TERM

wait
