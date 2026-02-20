#!/bin/bash

# ============================================
# Mango Sorter System - Startup Script
# Run: ./start-all.sh
# Stop: Press Ctrl+C
# ============================================

echo "🚀 Starting Mango Sorter System..."

# Start Backend (Node.js)
echo "📦 Starting Backend..."
cd /home/thesis/thesis_website/backend
npm install > /dev/null 2>&1
npm start > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 3

# Start Frontend (React)
echo "🎨 Starting Frontend..."
cd /home/thesis/thesis_website/frontend
npm install > /dev/null 2>&1
GENERATE_SOURCEMAP=false npm start > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

# Wait for frontend to start
sleep 5

# Start Python Sorter
echo "🤖 Starting Python Sorter..."
python3 /home/thesis/mango-sorter.py > /tmp/sorter.log 2>&1 &
SORTER_PID=$!
echo "   Sorter PID: $SORTER_PID"

echo ""
echo "✅ All services started!"
echo "   Backend:  http://localhost:5000"
echo "   Frontend: http://localhost:3000"
echo "   Sorter:   Running"
echo ""
echo "📋 Logs:"
echo "   Backend:  tail -f /tmp/backend.log"
echo "   Frontend: tail -f /tmp/frontend.log"
echo "   Sorter:   tail -f /tmp/sorter.log"
echo ""
echo "Press Ctrl+C to stop all services..."

# Kill all services on Ctrl+C
trap "echo '🛑 Shutting down...'; kill $BACKEND_PID $FRONTEND_PID $SORTER_PID 2>/dev/null; exit" INT

wait
