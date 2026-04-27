#!/usr/bin/env python3
"""Run HK sync repeatedly and wait for next day reset until all HK codes have data."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def get_db_config() -> DBConfig:
    return DBConfig(
        host=os.getenv("MYSQL_HOST", "192.168.1.15"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "myuser"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "mydb"),
    )


def mysql_connect(cfg: DBConfig):
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def query_progress(cfg: DBConfig) -> dict:
    sql = """
    SELECT
      (SELECT COUNT(*) FROM stock_basic WHERE asset_type='H' AND list_status='L') AS basic_total,
      (SELECT COUNT(DISTINCT ts_code) FROM stock_daily WHERE asset_type='H') AS daily_codes,
      (SELECT COUNT(*) FROM stock_daily WHERE asset_type='H') AS daily_rows
    """
    with mysql_connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone() or {}
    basic_total = int(row.get("basic_total") or 0)
    daily_codes = int(row.get("daily_codes") or 0)
    daily_rows = int(row.get("daily_rows") or 0)
    missing_codes = max(0, basic_total - daily_codes)
    return {
        "basic_total": basic_total,
        "daily_codes": daily_codes,
        "daily_rows": daily_rows,
        "missing_codes": missing_codes,
    }


def next_run_after_reset(hour: int, minute: int) -> datetime:
    now = datetime.now(TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_once(
    concurrency: int,
    request_interval: float,
    rate_limit_sleep: float,
    retry_sleep: float,
    missing_only: bool,
    provider: str,
) -> int:
    cmd = [
        sys.executable,
        "scripts/run_hk_sync_all_concurrent.py",
        "--concurrency",
        str(concurrency),
        "--request-interval",
        str(request_interval),
        "--rate-limit-sleep",
        str(rate_limit_sleep),
        "--retry-sleep",
        str(retry_sleep),
    ]
    if missing_only:
        cmd.append("--missing-only")
    if provider:
        cmd.extend(["--provider", provider])
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), check=False)
    return int(proc.returncode)


def main():
    parser = argparse.ArgumentParser(description="Run HK sync repeatedly until no missing HK code.")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-interval", type=float, default=35.0)
    parser.add_argument("--rate-limit-sleep", type=float, default=70.0)
    parser.add_argument("--retry-sleep", type=float, default=15.0)
    parser.add_argument("--resume-hour", type=int, default=0, help="Next-day resume hour in Asia/Shanghai")
    parser.add_argument("--resume-minute", type=int, default=6, help="Next-day resume minute in Asia/Shanghai")
    parser.add_argument("--max-loops", type=int, default=0, help="0 means unlimited")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only fetch symbols with no HK daily rows yet.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="auto",
        help="Provider for HK daily sync, e.g. auto/akshare_sina/akshare_em/tushare/yfinance",
    )
    args = parser.parse_args()

    cfg = get_db_config()
    loops = 0
    while True:
        loops += 1
        before = query_progress(cfg)
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{now_str}] progress_before basic={before['basic_total']} daily_codes={before['daily_codes']} "
            f"missing={before['missing_codes']} daily_rows={before['daily_rows']}",
            flush=True,
        )
        if before["missing_codes"] <= 0:
            print("hk sync completed: no missing codes", flush=True)
            return

        rc = run_once(
            concurrency=args.concurrency,
            request_interval=args.request_interval,
            rate_limit_sleep=args.rate_limit_sleep,
            retry_sleep=args.retry_sleep,
            missing_only=args.missing_only,
            provider=args.provider,
        )
        after = query_progress(cfg)
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{now_str}] progress_after basic={after['basic_total']} daily_codes={after['daily_codes']} "
            f"missing={after['missing_codes']} daily_rows={after['daily_rows']} rc={rc}",
            flush=True,
        )
        if after["missing_codes"] <= 0:
            print("hk sync completed: no missing codes", flush=True)
            return

        if args.max_loops > 0 and loops >= args.max_loops:
            print(f"max loops reached: {loops}", flush=True)
            return

        resume_at = next_run_after_reset(args.resume_hour, args.resume_minute)
        sleep_sec = max(60, int((resume_at - datetime.now(TZ)).total_seconds()))
        print(
            f"waiting until next window {resume_at.strftime('%Y-%m-%d %H:%M:%S %Z')} sleep={sleep_sec}s",
            flush=True,
        )
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
