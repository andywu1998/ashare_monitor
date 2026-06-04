#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT_DIR="${ROOT_DIR:-$SCRIPT_DIR}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python3"
RECORD_DIR="$ROOT_DIR/record"
SYNC_LOCK_DIR="$RECORD_DIR/sync-csv.lock"
LARK_CLI_BIN="${LARK_CLI_BIN:-$(command -v lark-cli || true)}"
LARK_NODE_BIN="${LARK_NODE_BIN:-$(dirname "$LARK_CLI_BIN")}"
DIRECT_COMPANY=""
DIRECT_MESSAGE_ID=""

usage() {
  cat <<'EOF'
Usage:
  sh sync-run.sh
  sh sync-run.sh --company <公司名> [--message-id <message_id>]

Modes:
  no args:
    Sync incomplete Feishu Bitable records into tasks_template.csv, then
    generate and upload pending CSV tasks.

  --company:
    Generate one company report immediately and upload it to Feishu without
    reading or updating tasks_template.csv. If --message-id is omitted, this
    script generates a random message_id for the uploaded Feishu record.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --company)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --company" >&2
        exit 1
      fi
      DIRECT_COMPANY="$2"
      shift 2
      ;;
    --message-id)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --message-id" >&2
        exit 1
      fi
      DIRECT_MESSAGE_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ ! -x "$VENV_PY" ]; then
  echo "Python venv not found or not executable: $VENV_PY" >&2
  exit 1
fi

if [ -z "$LARK_CLI_BIN" ] || [ ! -x "$LARK_CLI_BIN" ]; then
  echo "lark-cli not found. Install it or set LARK_CLI_BIN." >&2
  exit 1
fi

mkdir -p "$RECORD_DIR"

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

if [ -n "$DIRECT_COMPANY" ]; then
  if [ -z "$DIRECT_MESSAGE_ID" ]; then
    DIRECT_MESSAGE_ID=$("$VENV_PY" -c 'import secrets; print("om_" + secrets.token_hex(16))')
  fi

  MESSAGE_LOCK="$RECORD_DIR/$DIRECT_MESSAGE_ID"
  if ! ( set -C; : > "$MESSAGE_LOCK" ) 2>/dev/null; then
    echo "message_id is already running, skip: $DIRECT_MESSAGE_ID"
    exit 0
  fi
  cleanup_direct_lock() {
    rm -f "$MESSAGE_LOCK"
  }
  trap cleanup_direct_lock EXIT INT TERM

  GENERATE_CMD="$ROOT_DIR/invest_community/tools/company_report/generate_company_report.py"
  UPLOAD_CMD="$ROOT_DIR/invest_community/tools/feishu/create_doc_from_md.py"

  echo "直接生成研报: $DIRECT_COMPANY message_id=$DIRECT_MESSAGE_ID"
  GENERATE_OUTPUT=$("$VENV_PY" "$GENERATE_CMD" "$DIRECT_COMPANY" --message-id "$DIRECT_MESSAGE_ID")
  printf '%s\n' "$GENERATE_OUTPUT"

  REPORT_PATH=$(printf '%s\n' "$GENERATE_OUTPUT" | sed -n 's/^已生成报告[：:][[:space:]]*//p' | tail -n 1)
  if [ -z "$REPORT_PATH" ] || [ ! -f "$REPORT_PATH" ]; then
    echo "无法解析或找到生成的研报文件: $REPORT_PATH" >&2
    exit 1
  fi

  echo "上传飞书: $REPORT_PATH"
  FEISHU_MESSAGE_ID="$DIRECT_MESSAGE_ID" "$VENV_PY" "$UPLOAD_CMD" "$REPORT_PATH"
  exit 0
fi

if mkdir "$SYNC_LOCK_DIR" 2>/dev/null; then
  cleanup_sync_lock() {
    rmdir "$SYNC_LOCK_DIR" 2>/dev/null || true
  }
  trap cleanup_sync_lock EXIT INT TERM
  "$VENV_PY" "$ROOT_DIR/invest_community/tools/feishu/bitable_debug.py" sync-csv --table-id tblnCWQYqUXp3tmU
  cleanup_sync_lock
  trap - EXIT INT TERM
else
  echo "sync-csv is already running, skip."
fi

"$VENV_PY" "$ROOT_DIR/invest_community/tools/company_report/run_tasks_from_csv.py" "$ROOT_DIR/invest_community/tools/company_report/tasks_template.csv" --record-dir "$RECORD_DIR"
