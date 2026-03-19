
# To Run: ./start-all.sh
# Stop: Press Ctrl+C

echo "Starting Mango Sorter System..."

# for starting Backend (Node.js)
echo "Starting Backend..."
cd /home/thesis/embedded/thesis_website/backend
npm install > /dev/null 2>&1
npm start > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 3

# for  starting Frontend (React)
echo "Starting Frontend..."
cd /home/thesis/embedded/thesis_website/frontend
npm install > /dev/null 2>&1
GENERATE_SOURCEMAP=false npm start > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

# Wait for frontend to start
sleep 5

# for starting Temperature Monitor
echo "Starting Temperature Monitor..."
python3 /home/thesis/embedded/temp_monitor.py > /tmp/temp_monitor.log 2>&1 &
TEMP_MONITOR_PID=$!
echo "   Temp Monitor PID: $TEMP_MONITOR_PID"

# for starting Camera Stream (for WebRTC)
echo "Starting Camera Stream..."
python3 /home/thesis/embedded/cam_stream.py > /tmp/cam_stream.log 2>&1 &
CAM_STREAM_PID=$!
echo "   Cam Stream PID: $CAM_STREAM_PID"

echo ""
echo " All services started!"
echo "   Backend:  http://localhost:5000"
echo "   Frontend: http://localhost:3000"
echo "   Temp monitor: running"
echo "   Cam stream: running"
echo ""
echo " Logs:"
echo "   Backend:  tail -f /tmp/backend.log"
echo "   Frontend: tail -f /tmp/frontend.log"
echo "   Temp monitor: tail -f /tmp/temp_monitor.log"
echo "   Cam stream: tail -f /tmp/cam_stream.log"
echo ""
echo "Press Ctrl+C to stop all services..."

# Kill all services using Ctrl+C
trap "echo ' Shutting down...'; kill $BACKEND_PID $FRONTEND_PID $TEMP_MONITOR_PID $CAM_STREAM_PID 2>/dev/null; exit" INT

wait
