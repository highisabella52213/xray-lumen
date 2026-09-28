#!/bin/sh
set -eu
if [ "$(id -u)" = 0 ]; then
  mkdir -p /data && chown -R lumen:lumen /data
  exec gosu lumen "$0" "$@"
fi
if [ "${1:-}" != "v32" ]; then exec "$@"; fi
PUBLIC_PORT="${PORT:-8080}"
export LUMEN_CONTROL_PORT="${LUMEN_CONTROL_PORT:-8001}"
export PORT="$LUMEN_CONTROL_PORT"
python main.py > /tmp/lumen-control.log 2>&1 &
CONTROL_PID=$!
cleanup(){ kill "$CONTROL_PID" "$EDGE_PID" 2>/dev/null || true; wait "$CONTROL_PID" "$EDGE_PID" 2>/dev/null || true; }
trap cleanup INT TERM EXIT
# Wait for the control plane without requiring external tools.
python - <<'PY'
import os,socket,time,sys
port=int(os.environ['LUMEN_CONTROL_PORT'])
for _ in range(200):
    try:
        with socket.create_connection(('127.0.0.1',port),.2): sys.exit(0)
    except OSError: time.sleep(.1)
raise SystemExit('Lumen control plane did not start')
PY
export PORT="$PUBLIC_PORT"
node /app/edge_gateway.js &
EDGE_PID=$!
wait -n "$CONTROL_PID" "$EDGE_PID"
status=$?
cleanup
exit "$status"
