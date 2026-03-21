# To Run: ./start-all.sh
# Stop: Press Ctrl+C

set -e
echo "Starting Mango Sorter System..."

# Create control file for hardware
mkdir -p /tmp
echo '{"running": false}' > /tmp/mangosort_control.json

# for starting Backend (Node.js)
echo "Starting Backend..."
cd /home/thesis/embedded/thesis_website/backend
npm install > /dev/null 2>&1 || { echo " Backend npm install failed"; exit 1; }
npm start > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "   ✓ Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 4

# for starting Frontend (React)
echo "Starting Frontend..."
cd /home/thesis/embedded/thesis_website/frontend
npm install > /dev/null 2>&1 || { echo " Frontend npm install failed"; exit 1; }
GENERATE_SOURCEMAP=false npm start > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   ✓ Frontend PID: $FRONTEND_PID"

# Wait for frontend to start
sleep 5

# for starting Temperature Monitor
echo "Starting Temperature Monitor..."
if [ -f /home/thesis/embedded/temp_monitor.py ]; then
  python3 /home/thesis/embedded/temp_monitor.py > /tmp/temp_monitor.log 2>&1 &
  TEMP_MONITOR_PID=$!
  echo "   ✓ Temp Monitor PID: $TEMP_MONITOR_PID"
else
  echo "   ⚠ temp_monitor.py not found (optional)"
fi

# for starting Camera Stream (MJPEG)
echo "Starting Camera Stream..."
if [ -f /home/thesis/embedded/cam_stream.py ]; then
  python3 /home/thesis/embedded/cam_stream.py > /tmp/cam_stream.log 2>&1 &
  CAM_STREAM_PID=$!
  echo "   ✓ Cam Stream PID: $CAM_STREAM_PID"
else
  echo "   ⚠ cam_stream.py not found (optional)"
fi

# for starting Servotest (Hardware Control)
echo "Starting Servotest (Hardware Control)..."
if [ -f /home/thesis/embedded/servotest.py ]; then
  python3 /home/thesis/embedded/servotest.py > /tmp/servotest.log 2>&1 &
  SERVOTEST_PID=$!
  echo "   ✓ Servotest PID: $SERVOTEST_PID"
else
  echo "   ⚠ servotest.py not found"
fi


echo ""
echo "════════════════════════════════════════════════════"
echo "✓ All services started successfully!"
echo "════════════════════════════════════════════════════"
echo ""
echo "Access the system:"
echo "   🌐 Frontend:  http://raspberrypi.local:3000"
echo "   🔧 Backend API:  http://localhost:5000"
echo "   📷 MJPEG Stream:  http://localhost:8081/mjpeg"
echo "   🤖 Hardware API:  http://localhost:5000"
echo ""
echo "Monitor logs in real-time:"
echo "   tail -f /tmp/backend.log"
echo "   tail -f /tmp/frontend.log"
echo "   tail -f /tmp/temp_monitor.log"
echo "   tail -f /tmp/cam_stream.log"
echo "   tail -f /tmp/servotest.log"
echo ""
echo "Camera troubleshooting:"
echo "   • Check if camera enabled: raspi-config → Interface → Camera"
echo "   • Test OpenCV: python3 -c \"import cv2; cap = cv2.VideoCapture(0); print('OK' if cap.isOpened() else 'FAIL')\""
echo "   • View MJPEG stream: curl http://localhost:8081/mjpeg"
echo "   • View camera logs: tail -f /tmp/cam_stream.log"
echo ""
echo "Servotest troubleshooting:"
echo "   • Check hardware status: curl http://localhost:5000/sensors"
echo "   • Test control API: curl -X POST http://localhost:5000/control -H 'Content-Type: application/json' -d '{\"action\":\"start\"}'"
echo "   • View servotest logs: tail -f /tmp/servotest.log"
echo "   • Check GPIO pins: gpio readall (pins 17,27,22 for IR sensors)"
echo "   • Test YOLO model: python3 -c \"from ultralytics import YOLO; model = YOLO('yolov8n.pt'); print('YOLO OK')\""
echo ""
echo "Dashboard Features:"
echo "   • Click 'Start New Batch' to begin (spawns hardware_controller.py)"
echo "   • Click 'Pause Batch' to pause (hardware stops)"
echo "   • Click 'Continue Batch' to resume (hardware restarts)"
echo "   • Click 'Stop Batch' to finish (hardware stops)"
echo ""
echo "Press Ctrl+C to stop all services..."
echo "════════════════════════════════════════════════════"
echo ""

# Kill all services using Ctrl+C
trap "echo ''; echo 'Shutting down all services...'; kill $BACKEND_PID $FRONTEND_PID $TEMP_MONITOR_PID $CAM_STREAM_PID $SERVOTEST_PID 2>/dev/null; echo 'All services stopped.'; exit 0" INT

wait

