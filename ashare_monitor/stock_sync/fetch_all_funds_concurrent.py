from __future__ import annotations

import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import execute_sql_file, upsert_stock_basic, upsert_stock_daily


CHUNK_DAYS = 365 * 3
REQUEST_INTERVAL_SECONDS = float(os.getenv("TS_REQUEST_INTERVAL_SECONDS", "0.20"))
RATE_LIMIT_SLEEP_SECONDS = float(os.getenv("TS_RATE_LIMIT_SLEEP_SECONDS", "20"))
MAX_API_RETRIES = 5
FAILURE_RETRY_SLEEP_SECONDS = float(os.getenv("TS_FAILURE_RETRY_SLEEP_SECONDS", "5"))

PRINT_LOCK = threading.Lock()
THREAD_LOCAL = threading.local()


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
            msg = str(exc)
            if "频率超限" in msg and attempt < MAX_API_RETRIES:
                log(f"rate_limited attempt={attempt}/{MAX_API_RETRIES} sleep={RATE_LIMIT_SLEEP_SECONDS}s kwargs={kwargs}")
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            if attempt < MAX_API_RETRIES:
                log(f"api_retry attempt={attempt}/{MAX_API_RETRIES} sleep={FAILURE_RETRY_SLEEP_SECONDS}s error={exc} kwargs={kwargs}")
                time.sleep(FAILURE_RETRY_SLEEP_SECONDS)
                continue
            raise


def fetch_fund_basic_rows(pro):
    fields = (
        "ts_code,name,management,custodian,fund_type,found_date,due_date,list_date,"
        "issue_date,delist_date,issue_amount,m_fee,c_fee,duration_year,p_value,"
        "min_amount,exp_return,benchmark,status,invest_type,type,trustee,purc_startdate,"
        "redm_startdate,market"
    )
    rows = []
    for market in ("E", "O"):
        df = call_with_retry(pro.fund_basic, market=market, status="L", fields=fields)
        if df is None or df.empty:
            continue
        for row in df.itertuples(index=False):
            rows.append(
                (
                    row.ts_code,
                    "F",
                    getattr(row, "ts_code", "").split(".")[0],
                    row.name,
                    None,
                    None,
                    getattr(row, "market", None),
                    None,
                    "L",
                    parse_date(getattr(row, "list_date", None)),
                    parse_date(getattr(row, "delist_date", None)),
                    None,
                    getattr(row, "management", None),
                    getattr(row, "custodian", None),
                    getattr(row, "invest_type", None),
                    getattr(row, "fund_type", None),
                    getattr(row, "benchmark", None),
                    parse_date(getattr(row, "due_date", None)),
                    getattr(row, "issue_amount", None),
                )
            )
    return rows


def fetch_fund_daily_rows(pro, ts_code: str, start_date: date, end_date: date):
    df = call_with_retry(
        pro.fund_daily,
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return []
    rows = []
    for row in df.itertuples(index=False):
        rows.append(
            (
                row.ts_code,
                parse_date(row.trade_date),
                "F",
                row.open,
                row.high,
                row.low,
                row.close,
                row.pre_close,
                getattr(row, "change"),
                row.pct_chg,
                row.vol,
                row.amount,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )
    return rows


def fetch_fund_nav_rows(pro, ts_code: str, start_date: date, end_date: date):
    df = call_with_retry(
        pro.fund_nav,
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return []
    rows = []
    for row in df.itertuples(index=False):
        trade_date = parse_date(getattr(row, "end_date", None))
        if trade_date is None:
            continue
        rows.append(
            (
                row.ts_code,
                trade_date,
                "F",
                getattr(row, "unit_nav", None),
                getattr(row, "unit_nav", None),
                getattr(row, "unit_nav", None),
                getattr(row, "unit_nav", None),
                None,
                None,
                None,
                None,
                None,
                parse_date(getattr(row, "ann_date", None)),
                getattr(row, "unit_nav", None),
                getattr(row, "accum_nav", None),
                getattr(row, "accum_div", None),
                getattr(row, "net_asset", None),
                getattr(row, "total_netasset", None),
                getattr(row, "adj_nav", None),
            )
        )
    return rows


def fetch_fund_full_history_rows(pro, ts_code: str, list_date: date | None, end_date: date):
    effective_start = list_date or date(1990, 1, 1)
    daily_rows = []
    nav_rows = []
    cursor = effective_start
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        daily_rows.extend(fetch_fund_daily_rows(pro, ts_code, cursor, chunk_end))
        nav_rows.extend(fetch_fund_nav_rows(pro, ts_code, cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_INTERVAL_SECONDS)

    by_date = {}
    for row in nav_rows:
        by_date[(row[0], row[1])] = row
    for row in daily_rows:
        by_date[(row[0], row[1])] = row
    return list(by_date.values())


def process_fund(index: int, total: int, ts_code: str, name: str, list_date: date | None, end_date: date):
    try:
        pro = get_thread_pro()
        rows = fetch_fund_full_history_rows(pro, ts_code, list_date, end_date)
        written = upsert_stock_daily(rows)
        log(f"[{index}/{total}] fund={ts_code} name={name} rows={len(rows)} written={written}")
        return {"status": "success", "written": written}
    except Exception as exc:
        log(f"[{index}/{total}] fund={ts_code} name={name} failed={exc}")
        return {"status": "failed", "written": 0}


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch full history fund data into stock_basic/stock_daily.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-funds", type=int, default=0, help="Debug limit, 0 means all.")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=REQUEST_INTERVAL_SECONDS,
        help="Sleep seconds between chunk requests for each fund.",
    )
    parser.add_argument(
        "--rate-limit-sleep",
        type=float,
        default=RATE_LIMIT_SLEEP_SECONDS,
        help="Sleep seconds when TuShare returns rate limit.",
    )
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=FAILURE_RETRY_SLEEP_SECONDS,
        help="Sleep seconds for generic API retry backoff.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")
    if args.request_interval < 0:
        raise ValueError("--request-interval must be >= 0")
    if args.rate_limit_sleep < 0:
        raise ValueError("--rate-limit-sleep must be >= 0")
    if args.retry_sleep < 0:
        raise ValueError("--retry-sleep must be >= 0")

    global REQUEST_INTERVAL_SECONDS
    global RATE_LIMIT_SLEEP_SECONDS
    global FAILURE_RETRY_SLEEP_SECONDS
    REQUEST_INTERVAL_SECONDS = float(args.request_interval)
    RATE_LIMIT_SLEEP_SECONDS = float(args.rate_limit_sleep)
    FAILURE_RETRY_SLEEP_SECONDS = float(args.retry_sleep)

    execute_sql_file("schema.sql")
    bootstrap = init_pro()
    basic_rows = fetch_fund_basic_rows(bootstrap)
    upsert_stock_basic(basic_rows)
    funds = [(row[0], row[3], row[9]) for row in basic_rows]
    if args.max_funds > 0:
        funds = funds[: args.max_funds]
    log(f"fund_basic_loaded={len(funds)}")
    log(
        "runtime_config "
        f"concurrency={args.concurrency} "
        f"request_interval={REQUEST_INTERVAL_SECONDS}s "
        f"rate_limit_sleep={RATE_LIMIT_SLEEP_SECONDS}s "
        f"retry_sleep={FAILURE_RETRY_SLEEP_SECONDS}s"
    )

    end_date = date.today()
    success = 0
    failed = 0
    total_written = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(process_fund, i, len(funds), ts_code, name, list_date, end_date)
            for i, (ts_code, name, list_date) in enumerate(funds, start=1)
        ]
        for future in as_completed(futures):
            result = future.result()
            total_written += int(result.get("written", 0))
            if result.get("status") == "success":
                success += 1
            else:
                failed += 1
    log(f"completed funds_total={len(funds)} success={success} failed={failed} total_rows={total_written}")


if __name__ == "__main__":
    main()
