#!/usr/bin/env bash

# To run: ./start-all.sh
# Stop: press Ctrl+C

set -euo pipefail
IFS=$'\n\t'

echo "Starting Mango Sorter System..."

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_LOG_DIR="/tmp"
mkdir -p "$TMP_LOG_DIR"

CONTROL_FILE="$TMP_LOG_DIR/mangosort_control.json"
echo '{"running": false}' > "$CONTROL_FILE"

BACKEND_DIR="$ROOT_DIR/thesis_website/backend"
FRONTEND_DIR="$ROOT_DIR/thesis_website/frontend"

log_and_start() {
  local name=$1
  local cmd=$2
  local log=$3

  echo "Starting $name..."
  eval "$cmd" > "$log" 2>&1 &
  local pid=$!
  echo "   ✓ $name PID: $pid"
  echo "$pid"
}

wait_for_port() {
  local host=$1
  local port=$2
  local timeout=${3:-20}
  local start
  start=$(date +%s)
  while ! nc -z "$host" "$port" >/dev/null 2>&1; do
    if (( $(date +%s) - start >= timeout )); then
      echo "ERROR: $host:$port not available after $timeout seconds" >&2
      return 1
    fi
    sleep 1
  done
  return 0
}

cd "$BACKEND_DIR"
if [ -f package.json ]; then
  npm ci --silent --no-audit || npm install --silent --no-audit
else
  echo "ERROR: package.json not found in $BACKEND_DIR" >&2
  exit 1
fi
BACKEND_PID=$(log_and_start "Backend" "npm start" "$TMP_LOG_DIR/backend.log")

if ! wait_for_port "127.0.0.1" 5000 20; then
  echo "Backend did not start in time." >&2
  exit 1
fi

cd "$FRONTEND_DIR"
if [ -f package.json ]; then
  npm ci --silent --no-audit || npm install --silent --no-audit
else
  echo "ERROR: package.json not found in $FRONTEND_DIR" >&2
  exit 1
fi
FRONTEND_PID=$(log_and_start "Frontend" "GENERATE_SOURCEMAP=false npm start" "$TMP_LOG_DIR/frontend.log")

# optional processes
TEMP_MONITOR_PID=""
if [ -f "$ROOT_DIR/temp_monitor.py" ]; then
  TEMP_MONITOR_PID=$(log_and_start "Temp Monitor" "python3 '$ROOT_DIR/temp_monitor.py'" "$TMP_LOG_DIR/temp_monitor.log")
  echo "   ✓ Waiting for temp monitor.."
  sleep 1
fi

CAM_STREAM_PID=""
if [ -f "$ROOT_DIR/cam_stream.py" ]; then
  CAM_STREAM_PID=$(log_and_start "Cam Stream" "python3 '$ROOT_DIR/cam_stream.py'" "$TMP_LOG_DIR/cam_stream.log")
  echo "   ✓ MJPEG stream available at http://127.0.0.1:8081/mjpeg"
  echo "   ✓ WebRTC offer endpoint at http://127.0.0.1:8081/offer"
  sleep 1
fi

cat <<EOF

════════════════════════════════════════════════════
✓ All services started successfully!
════════════════════════════════════════════════════

Access the system:
   🌐 Frontend:  http://localhost:3000
   🔧 Backend API:  http://localhost:5000
   📷 MJPEG stream:  http://localhost:8081/mjpeg
   📷 WebRTC endpoint:  http://localhost:8081/offer

Monitor logs in real-time:
   tail -f /tmp/backend.log /tmp/frontend.log /tmp/temp_monitor.log /tmp/cam_stream.log

Dashboard controls:
   Start = POST /api/hardware/start
   Pause = POST /api/hardware/pause
   Continue = POST /api/hardware/continue
   Stop = POST /api/hardware/stop

Press Ctrl+C to stop all services.
════════════════════════════════════════════════════
EOF

cleanup() {
  echo "\nShutting down all services..."
  kill ${BACKEND_PID:-} ${FRONTEND_PID:-} ${TEMP_MONITOR_PID:-} ${CAM_STREAM_PID:-} 2>/dev/null || true
  echo "All services stopped."
}

trap cleanup INT TERM EXIT

# keep script alive while background jobs run
wait

