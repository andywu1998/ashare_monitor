#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/vol1/1000/andyroot/code/invest"
CSV_PATH="$ROOT_DIR/invest_community/tools/company_report/tasks_template.csv"
RECORD_DIR="$ROOT_DIR/record"
HELPER_PY="$ROOT_DIR/invest_community/tools/company_report/enqueue_csv_task.py"
VENV_PY="$ROOT_DIR/myenv/bin/python3"

if [[ -f "$ROOT_DIR/invest_community/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/invest_community/.env"
  set +a
fi

if [[ -z "${ARK_API_KEY:-}" ]]; then
  echo "缺少环境变量 ARK_API_KEY，请在环境变量或 invest_community/.env 中配置" >&2
  exit 1
fi

usage() {
  cat <<'EOF'
Usage:
  sh /vol1/1000/andyroot/code/invest/run_report_test.sh <公司名称>

What it does:
  1. Append a pending task into tasks_template.csv with a random message_id
  2. Reuse the existing downstream chain to generate the report and upload it to Feishu

Example:
  sh /vol1/1000/andyroot/code/invest/run_report_test.sh 地平线机器人
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

COMPANY_NAME="$1"

if [[ ! -f "$VENV_PY" ]]; then
  echo "缺少虚拟环境 Python: $VENV_PY" >&2
  exit 1
fi

if [[ ! -f "$HELPER_PY" ]]; then
  echo "缺少任务脚本: $HELPER_PY" >&2
  exit 1
fi

echo "开始测试研报链路: $COMPANY_NAME"
echo "使用解释器: $VENV_PY"
echo "ARK_API_KEY 已设置"

"$VENV_PY" "$HELPER_PY" "$COMPANY_NAME" --csv "$CSV_PATH" --record-dir "$RECORD_DIR" --python "$VENV_PY"
