#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT_DIR="${ROOT_DIR:-$SCRIPT_DIR}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python3"
RECORD_DIR="$ROOT_DIR/record"
LOCK_DIR="$RECORD_DIR/sync-run.lock"
LARK_CLI_BIN="${LARK_CLI_BIN:-$(command -v lark-cli || true)}"
LARK_NODE_BIN="${LARK_NODE_BIN:-$(dirname "$LARK_CLI_BIN")}"

if [ ! -x "$VENV_PY" ]; then
  echo "Python venv not found or not executable: $VENV_PY" >&2
  exit 1
fi

if [ -z "$LARK_CLI_BIN" ] || [ ! -x "$LARK_CLI_BIN" ]; then
  echo "lark-cli not found. Install it or set LARK_CLI_BIN." >&2
  exit 1
fi

mkdir -p "$RECORD_DIR"

# Avoid duplicate processing when this script is triggered every 10 seconds.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "sync-run is already running, skip."
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

if [ -f "$ROOT_DIR/invest_community/.env" ]; then
  set -a
  . "$ROOT_DIR/invest_community/.env"
  set +a
fi

if [ -z "${ARK_API_KEY:-}" ]; then
  echo "ARK_API_KEY is required. Set it in environment or invest_community/.env." >&2
  exit 1
fi

export HOME="${HOME:-/home/admin}"
export USER="${USER:-admin}"
export LOGNAME="${LOGNAME:-admin}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export PATH="$LARK_NODE_BIN:$VENV_DIR/bin:${PATH:-}"
export LARK_CLI_BIN
export FEISHU_LARK_CLI_PROFILE="${FEISHU_LARK_CLI_PROFILE:-company_analyze}"

"$VENV_PY" "$ROOT_DIR/invest_community/tools/feishu/bitable_debug.py" sync-csv --table-id tblnCWQYqUXp3tmU
"$VENV_PY" "$ROOT_DIR/invest_community/tools/company_report/run_tasks_from_csv.py" "$ROOT_DIR/invest_community/tools/company_report/tasks_template.csv" --record-dir "$RECORD_DIR"
