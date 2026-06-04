#!/usr/bin/env python3
"""Run report generation + Feishu upload tasks from a CSV checklist."""

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "是", "已上传", "已完成"}


def _pick_column(columns, keywords, default_index=None):
    for key in keywords:
        for col in columns:
            if key in col:
                return col
    if default_index is not None and default_index < len(columns):
        return columns[default_index]
    return None


def _write_csv(path: Path, fieldnames, rows):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def _update_csv_row(
    path: Path,
    fieldnames,
    message_col: str,
    company_col: str,
    message_id: str,
    company: str,
    updates: dict[str, str],
) -> None:
    """Merge updates into the latest CSV copy to avoid clobbering other workers."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        latest_rows = list(reader)
        latest_fieldnames = list(reader.fieldnames or fieldnames)

    for col in fieldnames:
        if col not in latest_fieldnames:
            latest_fieldnames.append(col)
            for latest_row in latest_rows:
                latest_row.setdefault(col, "")

    matched = False
    for latest_row in latest_rows:
        same_message = message_id and (latest_row.get(message_col) or "").strip() == message_id
        same_company_without_message = (
            not message_id
            and (latest_row.get(company_col) or "").strip() == company
        )
        if same_message or same_company_without_message:
            latest_row.update(updates)
            matched = True
            break

    if not matched:
        return

    _write_csv(path, latest_fieldnames, latest_rows)


def _parse_report_path(value: str, csv_dir: Path) -> Path | None:
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = (csv_dir / p).resolve()
    return p


def _is_stale_marker(marker_path: Path, stale_seconds: int) -> bool:
    if stale_seconds <= 0 or not marker_path.exists():
        return False
    try:
        age_seconds = time.time() - marker_path.stat().st_mtime
    except OSError:
        return False
    return age_seconds >= stale_seconds


def _run_generate(company: str, script_path: Path, message_id: str) -> str:
    cmd = [sys.executable, str(script_path), company]
    if message_id:
        cmd += ["--message-id", message_id]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    for line in result.stdout.splitlines():
        if "已生成报告" in line:
            # Handle both Chinese colon (：) and ASCII colon (:)
            if "：" in line:
                _, _, path = line.partition("：")
            else:
                _, _, path = line.partition(":")
            return path.strip()

    raise RuntimeError("无法从输出中解析报告路径")


def _run_upload(markdown_path: Path, script_path: Path, message_id: str) -> None:
    env = os.environ.copy()
    if message_id:
        env["FEISHU_MESSAGE_ID"] = message_id
    result = subprocess.run(
        [sys.executable, str(script_path), str(markdown_path)],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run report tasks from CSV")
    parser.add_argument("csv", help="Path to CSV file")
    parser.add_argument("--record-dir", required=True, help="Directory for concurrency markers")
    parser.add_argument("--max-passes", type=int, default=5, help="Max iterations")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between rows")
    parser.add_argument(
        "--stale-marker-seconds",
        type=int,
        default=int(os.getenv("REPORT_TASK_STALE_MARKER_SECONDS", "900")),
        help="Reclaim marker files older than this many seconds",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.exists():
        print(f"CSV 不存在: {csv_path}")
        return 1

    record_dir = Path(args.record_dir).expanduser().resolve()
    record_dir.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    generate_script = script_dir / "generate_company_report.py"
    upload_script = script_dir.parent / "feishu" / "create_doc_from_md.py"

    if not generate_script.exists():
        print(f"生成脚本不存在: {generate_script}")
        return 1
    if not upload_script.exists():
        print(f"上传脚本不存在: {upload_script}")
        return 1

    for _ in range(args.max_passes):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames or []

        if not fieldnames:
            print("CSV 缺少表头")
            return 1

        company_col = _pick_column(fieldnames, ["公司", "company"], default_index=0)
        report_col = _pick_column(fieldnames, ["文件", "路径", "report"], default_index=1)
        upload_col = _pick_column(fieldnames, ["上传", "feishu"], default_index=2)
        gen_col = _pick_column(fieldnames, ["生成", "generated"], default_index=3)
        msg_col = _pick_column(fieldnames, ["message_id", "message id", "消息id", "消息"], default_index=None)

        if not company_col or not report_col or not upload_col:
            print("CSV 表头需要包含公司名/报告路径/是否上传字段")
            return 1

        schema_changed = False
        if gen_col is None:
            gen_col = "是否生成研报"
            fieldnames.append(gen_col)
            for row in rows:
                row.setdefault(gen_col, "")
            schema_changed = True

        if msg_col is None:
            msg_col = "message_id"
            fieldnames.append(msg_col)
            for row in rows:
                row.setdefault(msg_col, "")
            schema_changed = True

        if schema_changed:
            for row in rows:
                for col in fieldnames:
                    row.setdefault(col, "")
            _write_csv(csv_path, fieldnames, rows)

        all_done = True
        progressed = False

        for row in rows:
            company = (row.get(company_col) or "").strip()
            message_id = (row.get(msg_col) or "").strip()
            marker_path = None
            if not company:
                continue

            report_path_raw = (row.get(report_col) or "").strip()
            report_path = _parse_report_path(report_path_raw, csv_path.parent)
            uploaded = _truthy(row.get(upload_col, ""))
            needs_generate = report_path is None or not report_path.exists()
            needs_upload = report_path is not None and report_path.exists() and not uploaded
            if not needs_generate and not needs_upload:
                continue

            if message_id:
                marker_path = record_dir / message_id
                try:
                    marker_path.open("x").close()
                except FileExistsError:
                    if _is_stale_marker(marker_path, args.stale_marker_seconds):
                        try:
                            marker_path.unlink()
                            print(f"清理陈旧标记: {marker_path.name}")
                            marker_path.open("x").close()
                        except OSError as exc:
                            print(f"无法回收陈旧标记 {marker_path}: {exc}")
                            all_done = False
                            continue
                    else:
                        all_done = False
                        continue
                except OSError as exc:
                    print(f"无法创建标记文件 {marker_path}: {exc}")
                    all_done = False
                    continue

            try:
                if needs_generate:
                    all_done = False
                    print(f"生成研报: {company}")
                    try:
                        output_path = _run_generate(company, generate_script, message_id)
                        row[report_col] = output_path
                        row[gen_col] = "是"
                        _update_csv_row(
                            csv_path,
                            fieldnames,
                            msg_col,
                            company_col,
                            message_id,
                            company,
                            {report_col: output_path, gen_col: "是"},
                        )
                        progressed = True
                        report_path = Path(output_path)
                    except Exception as exc:
                        print(f"生成失败: {company} - {exc}")
                        continue

                if report_path is not None and report_path.exists() and not uploaded:
                    all_done = False
                    print(f"上传飞书: {company}")
                    try:
                        _run_upload(report_path, upload_script, message_id)
                        row[upload_col] = "是"
                        _update_csv_row(
                            csv_path,
                            fieldnames,
                            msg_col,
                            company_col,
                            message_id,
                            company,
                            {upload_col: "是"},
                        )
                        progressed = True
                    except Exception as exc:
                        print(f"上传失败: {company} - {exc}")

                time.sleep(args.delay)
            finally:
                if marker_path and marker_path.exists():
                    try:
                        marker_path.unlink()
                    except OSError as exc:
                        print(f"警告: 无法清理标记文件 {marker_path}: {exc}")

        if all_done:
            print("所有任务已完成")
            return 0
        if not progressed:
            print("本轮无进展，停止以避免死循环")
            return 1

    print("达到最大轮次仍未完成")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
