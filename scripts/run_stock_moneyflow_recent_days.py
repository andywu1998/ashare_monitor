#!/usr/bin/env python3
"""Backfill A-share moneyflow by trade date (one request per day)."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import time
import shlex

import pandas as pd
import tushare as ts

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        try:
            parsed = shlex.split(value)
            os.environ[key] = parsed[0] if parsed else ""
        except Exception:
            os.environ[key] = value.strip("'\"")


load_env_file(Path.home() / ".config" / "ashare_monitor" / "cycle_web.env")

from ashare_monitor.stock_sync.config import TUSHARE_TOKEN
from ashare_monitor.stock_sync.db import execute_sql_file, upsert_stock_moneyflow


RATE_LIMIT_SLEEP_SECONDS = 65
GENERAL_RETRY_SLEEP_SECONDS = 5
MAX_API_RETRIES = 5
REQUEST_INTERVAL_SECONDS = 0.2
DEFAULT_TRADE_DAYS_ONE_YEAR = 252
MONEYFLOW_FIELD_NAMES = (
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch recent N trade days moneyflow for all A-shares by trade_date (default: ~1 year)."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_TRADE_DAYS_ONE_YEAR,
        help=f"Recent N trade days (default {DEFAULT_TRADE_DAYS_ONE_YEAR}, about 1 trading year).",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="End date YYYY-MM-DD (default today).",
    )
    parser.add_argument(
        "--exchange",
        default="SSE",
        help="Trade calendar exchange code, e.g. SSE/SZSE.",
    )
    parser.add_argument(
        "--calendar-lookback-days",
        type=int,
        default=180,
        help="Calendar days to backtrack for trade_cal.",
    )
    return parser.parse_args()


def call_with_retry(api_func, **kwargs):
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            return api_func(**kwargs)
        except Exception as exc:
            message = str(exc)
            if "频率超限" in message and attempt < MAX_API_RETRIES:
                print(
                    f"rate_limited attempt={attempt}/{MAX_API_RETRIES} sleep={RATE_LIMIT_SLEEP_SECONDS}s kwargs={kwargs}",
                    flush=True,
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            if attempt < MAX_API_RETRIES:
                print(
                    f"api_retry attempt={attempt}/{MAX_API_RETRIES} sleep={GENERAL_RETRY_SLEEP_SECONDS}s error={exc} kwargs={kwargs}",
                    flush=True,
                )
                time.sleep(GENERAL_RETRY_SLEEP_SECONDS)
                continue
            raise


def init_pro():
    if not TUSHARE_TOKEN:
        raise ValueError("Missing TUSHARE_TOKEN")
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()


def resolve_trade_days(
    pro,
    days: int,
    end_date: date | None,
    calendar_lookback_days: int,
    exchange: str,
) -> list[str]:
    real_end = end_date or date.today()
    cal_start = real_end - timedelta(days=max(days * 6, calendar_lookback_days))
    cal = call_with_retry(
        pro.trade_cal,
        exchange=exchange,
        start_date=cal_start.strftime("%Y%m%d"),
        end_date=real_end.strftime("%Y%m%d"),
        is_open=1,
        fields="cal_date,is_open",
    )
    if cal is None or cal.empty:
        raise RuntimeError("trade_cal returned empty data")

    trade_days = sorted(str(x) for x in cal["cal_date"].tolist())
    if len(trade_days) < days:
        raise RuntimeError(
            f"not enough trade days: required={days} got={len(trade_days)}; try larger --calendar-lookback-days"
        )
    return trade_days[-days:]


def to_row(row) -> tuple:
    trade_date = pd.to_datetime(
        str(getattr(row, "trade_date", "")), format="%Y%m%d", errors="coerce"
    )
    if pd.isna(trade_date):
        raise ValueError(f"invalid trade_date={getattr(row, 'trade_date', None)}")
    return (
        getattr(row, "ts_code", None),
        trade_date.date(),
        "E",
        *(getattr(row, field, None) for field in MONEYFLOW_FIELD_NAMES),
    )


def main() -> None:
    args = parse_args()
    if args.days < 1:
        raise ValueError("--days must be >= 1")

    execute_sql_file("schema.sql")
    pro = init_pro()
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    trade_days = resolve_trade_days(
        pro,
        days=args.days,
        end_date=end_date,
        calendar_lookback_days=args.calendar_lookback_days,
        exchange=args.exchange,
    )
    print(
        f"moneyflow_trade_days={len(trade_days)} samples={trade_days[0]}..{trade_days[-1]}",
        flush=True,
    )

    total_fetched = 0
    total_written = 0
    success = 0
    failed = 0

    for idx, td in enumerate(trade_days, start=1):
        try:
            df = call_with_retry(pro.moneyflow, trade_date=td)
            rows = []
            if df is not None and not df.empty:
                rows = [to_row(row) for row in df.itertuples(index=False)]
            written = upsert_stock_moneyflow(rows)
            total_fetched += len(rows)
            total_written += written
            success += 1
            print(
                f"[{idx}/{len(trade_days)}] trade_date={td} rows_fetched={len(rows)} rows_written={written}",
                flush=True,
            )
            time.sleep(REQUEST_INTERVAL_SECONDS)
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{len(trade_days)}] trade_date={td} failed={exc}", flush=True)

    print(
        f"completed days={len(trade_days)} success={success} failed={failed} total_fetched={total_fetched} total_written={total_written}",
        flush=True,
    )


if __name__ == "__main__":
    main()
