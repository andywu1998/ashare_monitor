#!/usr/bin/env python3
"""Append a pending company-report task into the existing CSV queue and optionally run it."""

import argparse
import csv
import secrets
import subprocess
import sys
from pathlib import Path


def _build_message_id(existing_ids: set[str]) -> str:
    while True:
        message_id = f"om_{secrets.token_hex(16)}"
        if message_id not in existing_ids:
            return message_id


def _default_runner_python() -> Path:
    project_root = Path(__file__).resolve().parents[3]
    venv_python = project_root / "myenv" / "bin" / "python3"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a pending task into the report CSV queue")
    parser.add_argument("company", help="Company name")
    parser.add_argument(
        "--csv",
        default=str(Path(__file__).with_name("tasks_template.csv")),
        help="Path to the task CSV",
    )
    parser.add_argument(
        "--record-dir",
        default=str(Path(__file__).resolve().parents[3] / "record"),
        help="Directory for concurrency markers",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Only enqueue the task without invoking the downstream processing chain",
    )
    parser.add_argument(
        "--python",
        default=str(_default_runner_python()),
        help="Python executable used to run the downstream processing chain",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.exists():
        raise SystemExit(f"CSV 不存在: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    required = ["公司名称", "研报文件路径", "是否上传飞书", "是否生成研报", "message_id"]
    if fieldnames != required:
        raise SystemExit(f"CSV 表头不符合预期: {fieldnames}")

    existing_ids = {(row.get("message_id") or "").strip() for row in rows if row.get("message_id")}
    message_id = _build_message_id(existing_ids)

    rows.append(
        {
            "公司名称": args.company.strip(),
            "研报文件路径": "",
            "是否上传飞书": "",
            "是否生成研报": "",
            "message_id": message_id,
        }
    )

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=required)
        writer.writeheader()
        writer.writerows(rows)

    print(f"已追加任务: company={args.company.strip()} message_id={message_id}")
    print(f"CSV: {csv_path}")

    if args.skip_run:
        return 0

    record_dir = Path(args.record_dir).expanduser().resolve()
    runner = Path(__file__).with_name("run_tasks_from_csv.py")
    python_executable = Path(args.python).expanduser()
    cmd = [str(python_executable), str(runner), str(csv_path), "--record-dir", str(record_dir)]
    print("开始执行后续处理链路:")
    print(" ".join(cmd))

    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
