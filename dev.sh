#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Activate venv and start Python backend
source .venv/bin/activate
python main.py &
BACKEND_PID=$!

# Start Vite frontend
npm run dev &
VITE_PID=$!

# Kill both on exit
trap "kill $BACKEND_PID $VITE_PID 2>/dev/null" EXIT
wait
