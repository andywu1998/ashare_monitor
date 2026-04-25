#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV_PY="$ROOT_DIR/.venv_web/bin/python"
APP="services.cycle_web.app:app"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8888}"
PID_FILE="$ROOT_DIR/logs/cycle_web.pid"
PORT_FILE="$ROOT_DIR/logs/cycle_web.port"
HOST_FILE="$ROOT_DIR/logs/cycle_web.host"
LOG_FILE="$ROOT_DIR/logs/cycle_web.log"

usage() {
  cat <<USAGE
Usage:
  sh ./start_service.sh [start|stop|restart|status|logs]

Defaults:
  HOST=0.0.0.0
  PORT=8888

Examples:
  sh ./start_service.sh start
  HOST=0.0.0.0 PORT=8888 sh ./start_service.sh start
USAGE
}

ensure_dirs() {
  mkdir -p "$ROOT_DIR/logs"
}

is_running() {
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

read_state_port() {
  if [ -f "$PORT_FILE" ]; then
    cat "$PORT_FILE" 2>/dev/null || true
  fi
}

read_state_host() {
  if [ -f "$HOST_FILE" ]; then
    cat "$HOST_FILE" 2>/dev/null || true
  fi
}

port_listener_pid() {
  p="$1"
  lsof -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

port_bindable() {
  h="$1"
  p="$2"
  "$VENV_PY" - "$h" "$p" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

start() {
  ensure_dirs

  if [ ! -x "$VENV_PY" ]; then
    echo "[error] Python not found: $VENV_PY"
    exit 1
  fi

  if is_running; then
    pid=$(cat "$PID_FILE")
    cur_port=$(read_state_port)
    cur_host=$(read_state_host)
    echo "[ok] service already running, pid=$pid host=${cur_host:-unknown} port=${cur_port:-unknown}"
    exit 0
  fi

  selected_port="$PORT"
  if ! port_bindable "$HOST" "$selected_port"; then
    echo "[error] requested port not bindable: $selected_port"
    exit 1
  fi

  listener_pid=$(port_listener_pid "$selected_port")
  if [ -n "$listener_pid" ]; then
    echo "[error] port $selected_port is already in use by pid=$listener_pid"
    exit 1
  fi

  nohup "$VENV_PY" -m uvicorn "$APP" --host "$HOST" --port "$selected_port" >"$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "$selected_port" > "$PORT_FILE"
  echo "$HOST" > "$HOST_FILE"

  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    echo "[ok] service started"
    echo "      pid=$pid"
    echo "      bind=${HOST}:${selected_port}"
    echo "      url=http://127.0.0.1:$selected_port/ui/"
    echo "      health=http://127.0.0.1:$selected_port/api/health"
    echo "      log=$LOG_FILE"
  else
    echo "[error] service failed to start, check log: $LOG_FILE"
    rm -f "$PID_FILE" "$PORT_FILE" "$HOST_FILE"
    exit 1
  fi
}

stop() {
  if ! is_running; then
    echo "[ok] service not running"
    rm -f "$PID_FILE" "$PORT_FILE" "$HOST_FILE"
    exit 0
  fi

  pid=$(cat "$PID_FILE")
  kill "$pid" 2>/dev/null || true
  sleep 1

  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$PID_FILE" "$PORT_FILE" "$HOST_FILE"
  echo "[ok] service stopped (pid=$pid)"
}

status() {
  cur_port="${PORT:-$(read_state_port)}"
  if [ -z "$cur_port" ]; then
    cur_port="$PORT"
  fi

  if is_running; then
    echo "[ok] running, pid=$(cat "$PID_FILE")"
  else
    echo "[ok] not running"
  fi

  listener_pid=$(port_listener_pid "$cur_port")
  if [ -n "$listener_pid" ]; then
    echo "[info] port $cur_port listener pid=$listener_pid"
  else
    echo "[info] port $cur_port has no listener"
  fi
}

show_logs() {
  if [ -f "$LOG_FILE" ]; then
    tail -n 120 "$LOG_FILE"
  else
    echo "[info] no log file yet: $LOG_FILE"
  fi
}

cmd="${1:-start}"
case "$cmd" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) show_logs ;;
  -h|--help|help) usage ;;
  *)
    echo "[error] unknown command: $cmd"
    usage
    exit 1
    ;;
esac
