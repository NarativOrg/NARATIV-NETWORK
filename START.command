#!/usr/bin/env bash
# Narativ Network — start admin + preview
# Double-click this in Finder to launch.

cd "$(dirname "$0")"

# Use the venv if it exists, else fall back to system nn
NN=".venv/bin/nn"
[[ -x "$NN" ]] || NN="nn"

echo "Starting admin dashboard..."
"$NN" admin &
ADMIN_PID=$!

echo "Starting live preview..."
"$NN" preview &
PREVIEW_PID=$!

# Give servers 2 seconds to come up, then open browser
sleep 2
open "http://127.0.0.1:8765"

echo ""
echo "  Admin   → http://127.0.0.1:8765"
echo "  Preview → http://127.0.0.1:8888/live.m3u8  (open in Safari)"
echo ""
echo "  Press Ctrl+C to stop both."

# Wait and clean up on exit
trap "kill $ADMIN_PID $PREVIEW_PID 2>/dev/null" INT TERM EXIT
wait $ADMIN_PID $PREVIEW_PID
