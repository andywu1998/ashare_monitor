from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import (
    execute_sql_file,
    fetch_daily_row_counts,
    fetch_stock_basic_rows,
    fetch_stock_basic_ts_codes,
    upsert_stock_basic,
    upsert_stock_daily,
)


CHUNK_DAYS = 365 * 3
REQUEST_INTERVAL_SECONDS = 0.25
RATE_LIMIT_SLEEP_SECONDS = 65
MAX_API_RETRIES = 5
MIN_EXISTING_ROWS_TO_SKIP = 700
FAILURE_ALERT_THRESHOLD = 3
FAILURE_RETRY_SLEEP_SECONDS = 10
ALERT_RED = "\033[1;37;41m"
ALERT_RESET = "\033[0m"

PRINT_LOCK = threading.Lock()
PROMPT_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
THREAD_LOCAL = threading.local()
ALERT_MODE = "prompt"


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def parse_date(value: str):
    if value is None:
        return None
    if pd.isna(value):
        return None

    text = str(value).strip()
    if not text or text.lower() == "nat":
        return None

    return pd.to_datetime(text, format="%Y%m%d").date()


def init_pro():
    if not TUSHARE_TOKEN:
        raise ValueError("Missing TUSHARE_TOKEN")

    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()


def get_thread_pro():
    pro = getattr(THREAD_LOCAL, "pro", None)
    if pro is None:
        pro = init_pro()
        THREAD_LOCAL.pro = pro
    return pro


def call_with_retry(api_func, **kwargs):
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            return api_func(**kwargs)
        except Exception as exc:
            message = str(exc)
            if "频率超限" in message and attempt < MAX_API_RETRIES:
                log(
                    f"rate_limited attempt={attempt}/{MAX_API_RETRIES} sleep={RATE_LIMIT_SLEEP_SECONDS}s kwargs={kwargs}"
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            if attempt < MAX_API_RETRIES:
                log(
                    f"api_retry attempt={attempt}/{MAX_API_RETRIES} error={exc} kwargs={kwargs}"
                )
                time.sleep(FAILURE_RETRY_SLEEP_SECONDS)
                continue
            raise


def fetch_stock_basic(pro):
    df = call_with_retry(
        pro.stock_basic,
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs",
    )
    if df.empty:
        return []

    rows = []
    for row in df.itertuples(index=False):
        rows.append(
            (
                row.ts_code,
                row.symbol,
                row.name,
                row.area,
                row.industry,
                row.market,
                row.exchange,
                row.list_status,
                parse_date(row.list_date),
                parse_date(row.delist_date),
                row.is_hs,
            )
        )
    return rows


def fetch_daily_rows(pro, ts_code: str, start_date: date, end_date: date):
    df = call_with_retry(
        pro.daily,
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    if df.empty:
        return []

    rows = []
    for row in df.itertuples(index=False):
        rows.append(
            (
                row.ts_code,
                parse_date(row.trade_date),
                row.open,
                row.high,
                row.low,
                row.close,
                row.pre_close,
                getattr(row, "change"),
                row.pct_chg,
                row.vol,
                row.amount,
            )
        )
    return rows


def fetch_full_history_rows(pro, ts_code: str, list_date: date | None, end_date: date):
    effective_start = list_date or date(1990, 1, 1)
    rows = []
    cursor = effective_start

    while cursor <= end_date:
        if STOP_EVENT.is_set():
            raise RuntimeError("stopped_by_user")
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        rows.extend(fetch_daily_rows(pro, ts_code, cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_INTERVAL_SECONDS)

    return rows


def prompt_after_alert(ts_code: str, name: str, failure_count: int, exc: Exception) -> str:
    if ALERT_MODE == "continue":
        log(
            f"{ALERT_RED}ALERT ts_code={ts_code} name={name} consecutive_failures={failure_count} error={exc} action=auto_continue{ALERT_RESET}"
        )
        return "c"

    if ALERT_MODE == "stop":
        log(
            f"{ALERT_RED}ALERT ts_code={ts_code} name={name} consecutive_failures={failure_count} error={exc} action=auto_stop{ALERT_RESET}"
        )
        return "q"

    with PROMPT_LOCK:
        log(
            f"{ALERT_RED}ALERT ts_code={ts_code} name={name} consecutive_failures={failure_count} error={exc} 输入 c 继续重试，输入 q 停止全部任务{ALERT_RESET}"
        )
        while True:
            choice = input("continue or quit? [c/q]: ").strip().lower()
            if choice in {"c", "q"}:
                return choice


def process_stock(index: int, total: int, ts_code: str, name: str, list_date: date | None, existing_rows: int, end_date: date):
    if existing_rows > MIN_EXISTING_ROWS_TO_SKIP:
        log(
            f"[{index}/{total}] ts_code={ts_code} name={name} skipped existing_rows={existing_rows}"
        )
        return {"status": "skipped", "written": 0}

    consecutive_failures = 0
    attempt = 0

    while not STOP_EVENT.is_set():
        attempt += 1
        try:
            pro = get_thread_pro()
            rows = fetch_full_history_rows(pro, ts_code, list_date, end_date)
            if not rows:
                raise RuntimeError("empty_rows")
            written = upsert_stock_daily(rows)
            log(
                f"[{index}/{total}] ts_code={ts_code} name={name} existing_rows={existing_rows} fetched={len(rows)} written={written} attempt={attempt}"
            )
            return {"status": "success", "written": written}
        except Exception as exc:
            consecutive_failures += 1
            log(
                f"[{index}/{total}] ts_code={ts_code} name={name} attempt={attempt} consecutive_failures={consecutive_failures} failed={exc}"
            )
            if STOP_EVENT.is_set():
                break
            if consecutive_failures >= FAILURE_ALERT_THRESHOLD:
                choice = prompt_after_alert(ts_code, name, consecutive_failures, exc)
                if choice == "q":
                    STOP_EVENT.set()
                    return {"status": "stopped", "written": 0}
                consecutive_failures = 0
            time.sleep(FAILURE_RETRY_SLEEP_SECONDS)

    return {"status": "stopped", "written": 0}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Concurrent full-history stock fetcher with retry and alerting."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent worker threads.",
    )
    parser.add_argument(
        "--on-alert",
        choices=("prompt", "continue", "stop"),
        default="prompt",
        help="Action after repeated failures: prompt in foreground, or auto-continue/auto-stop for background runs.",
    )
    return parser.parse_args()


def main():
    global ALERT_MODE
    args = parse_args()
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")
    ALERT_MODE = args.on_alert

    execute_sql_file("schema.sql")
    bootstrap_pro = init_pro()

    basic_ts_codes = fetch_stock_basic_ts_codes()
    if basic_ts_codes:
        log(f"stock_basic_loaded={len(basic_ts_codes)} source=mysql")
    else:
        basic_rows = fetch_stock_basic(bootstrap_pro)
        upsert_stock_basic(basic_rows)
        basic_ts_codes = [row[0] for row in basic_rows]
        log(f"stock_basic_written={len(basic_ts_codes)} source=tushare")

    stock_rows = fetch_stock_basic_rows()
    basic_ts_code_set = set(basic_ts_codes)
    existing_row_counts = fetch_daily_row_counts()
    pending_stocks = [
        (ts_code, name, list_date, existing_row_counts.get(ts_code, 0))
        for ts_code, name, list_date in stock_rows
        if ts_code in basic_ts_code_set
    ]

    skip_count = sum(1 for _, _, _, row_count in pending_stocks if row_count > MIN_EXISTING_ROWS_TO_SKIP)
    log(
        f"daily_progress total={len(pending_stocks)} skipped={skip_count} pending={len(pending_stocks) - skip_count} mode=full_history_concurrent concurrency={args.concurrency} on_alert={args.on_alert}"
    )

    end_date = date.today()
    success = 0
    skipped = 0
    stopped = 0
    failed = 0
    total_written = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                process_stock,
                index,
                len(pending_stocks),
                ts_code,
                name,
                list_date,
                existing_rows,
                end_date,
            )
            for index, (ts_code, name, list_date, existing_rows) in enumerate(
                pending_stocks, start=1
            )
        ]

        for future in as_completed(futures):
            result = future.result()
            status = result["status"]
            total_written += result["written"]
            if status == "success":
                success += 1
            elif status == "skipped":
                skipped += 1
            elif status == "stopped":
                stopped += 1
            else:
                failed += 1

            if STOP_EVENT.is_set():
                for pending_future in futures:
                    pending_future.cancel()
                break

    log(
        f"completed total_stocks={len(pending_stocks)} success={success} skipped={skipped} failed={failed} stopped={stopped} total_daily_rows={total_written}"
    )


if __name__ == "__main__":
    main()
