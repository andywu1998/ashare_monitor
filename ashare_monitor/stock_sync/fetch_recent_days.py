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
    fetch_stock_basic_rows,
    fetch_stock_basic_ts_codes,
    upsert_market_breadth_daily_for_range,
    upsert_stock_basic,
    upsert_stock_daily,
)


REQUEST_INTERVAL_SECONDS = 0.15
RATE_LIMIT_SLEEP_SECONDS = 65
GENERAL_RETRY_SLEEP_SECONDS = 5
MAX_API_RETRIES = 5
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
            message = str(exc)
            if "频率超限" in message and attempt < MAX_API_RETRIES:
                log(
                    f"rate_limited attempt={attempt}/{MAX_API_RETRIES} sleep={RATE_LIMIT_SLEEP_SECONDS}s kwargs={kwargs}"
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            if attempt < MAX_API_RETRIES:
                log(
                    f"api_retry attempt={attempt}/{MAX_API_RETRIES} sleep={GENERAL_RETRY_SLEEP_SECONDS}s error={exc} kwargs={kwargs}"
                )
                time.sleep(GENERAL_RETRY_SLEEP_SECONDS)
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


def resolve_trade_window(
    pro,
    days: int,
    end_date: date | None,
    calendar_lookback_days: int,
    exchange: str,
):
    real_end = end_date or date.today()
    cal_start = real_end - timedelta(days=max(days * 4, calendar_lookback_days))
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

    selected = trade_days[-days:]
    start = parse_date(selected[0])
    end = parse_date(selected[-1])
    if start is None or end is None:
        raise RuntimeError("failed to parse trade window")
    return start, end, selected


def fetch_daily_rows(pro, ts_code: str, start_date: date, end_date: date):
    df = call_with_retry(
        pro.daily,
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    if df.empty:
        return []

    moneyflow_map = {}
    try:
        mf_df = call_with_retry(
            pro.moneyflow,
            ts_code=ts_code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if mf_df is not None and not mf_df.empty:
            for row in mf_df.itertuples(index=False):
                trade_date = parse_date(getattr(row, "trade_date", None))
                if trade_date is None:
                    continue
                moneyflow_map[trade_date] = {
                    field: getattr(row, field, None) for field in MONEYFLOW_FIELD_NAMES
                }
    except Exception as exc:
        log(f"moneyflow_fallback ts_code={ts_code} error={exc}")

    rows = []
    for row in df.itertuples(index=False):
        trade_date = parse_date(row.trade_date)
        mf = moneyflow_map.get(trade_date, {})
        rows.append(
            (
                row.ts_code,
                trade_date,
                "E",
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
                mf.get("buy_sm_vol"),
                mf.get("buy_sm_amount"),
                mf.get("sell_sm_vol"),
                mf.get("sell_sm_amount"),
                mf.get("buy_md_vol"),
                mf.get("buy_md_amount"),
                mf.get("sell_md_vol"),
                mf.get("sell_md_amount"),
                mf.get("buy_lg_vol"),
                mf.get("buy_lg_amount"),
                mf.get("sell_lg_vol"),
                mf.get("sell_lg_amount"),
                mf.get("buy_elg_vol"),
                mf.get("buy_elg_amount"),
                mf.get("sell_elg_vol"),
                mf.get("sell_elg_amount"),
                mf.get("net_mf_vol"),
                mf.get("net_mf_amount"),
            )
        )
    return rows


def process_stock(index: int, total: int, ts_code: str, name: str, start_date: date, end_date: date):
    try:
        pro = get_thread_pro()
        daily_rows = fetch_daily_rows(pro, ts_code, start_date, end_date)
        written = upsert_stock_daily(daily_rows)
        time.sleep(REQUEST_INTERVAL_SECONDS)
        log(
            f"[{index}/{total}] ts_code={ts_code} name={name} rows_fetched={len(daily_rows)} rows_written={written}"
        )
        return {"status": "ok", "written": written, "fetched": len(daily_rows)}
    except Exception as exc:
        log(f"[{index}/{total}] ts_code={ts_code} name={name} failed={exc}")
        return {"status": "failed", "written": 0, "fetched": 0, "error": str(exc)}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch all A-share stocks for the latest N trade days and upsert into MySQL."
    )
    parser.add_argument("--days", type=int, default=10, help="Latest N trade days to sync.")
    parser.add_argument("--concurrency", type=int, default=4, help="Worker threads.")
    parser.add_argument(
        "--end-date",
        default="",
        help="Trade window end date YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--calendar-lookback-days",
        type=int,
        default=60,
        help="Calendar days used to backtrack exchange trade calendar.",
    )
    parser.add_argument(
        "--exchange",
        default="SSE",
        help="Trade calendar exchange code, e.g. SSE or SZSE.",
    )
    parser.add_argument(
        "--max-stocks",
        type=int,
        default=0,
        help="Debug only: limit stocks to first N (0 means all).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.days < 1:
        raise ValueError("--days must be >= 1")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")

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
    valid_codes = set(basic_ts_codes)
    pending_stocks = [
        (ts_code, name)
        for ts_code, name, _list_date in stock_rows
        if ts_code in valid_codes
    ]
    if args.max_stocks > 0:
        pending_stocks = pending_stocks[: args.max_stocks]

    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    start_date, real_end_date, trade_days = resolve_trade_window(
        bootstrap_pro,
        days=args.days,
        end_date=end_date,
        calendar_lookback_days=args.calendar_lookback_days,
        exchange=args.exchange,
    )
    log(
        f"trade_window start={start_date} end={real_end_date} trade_days={len(trade_days)} samples={trade_days[0]}..{trade_days[-1]}"
    )
    log(
        f"daily_progress total={len(pending_stocks)} days={args.days} concurrency={args.concurrency}"
    )

    success = 0
    failed = 0
    total_fetched = 0
    total_written = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                process_stock,
                index,
                len(pending_stocks),
                ts_code,
                name,
                start_date,
                real_end_date,
            )
            for index, (ts_code, name) in enumerate(pending_stocks, start=1)
        ]

        for future in as_completed(futures):
            result = future.result()
            total_fetched += int(result.get("fetched", 0))
            total_written += int(result.get("written", 0))
            if result.get("status") == "ok":
                success += 1
            else:
                failed += 1

    breadth_rows = upsert_market_breadth_daily_for_range(start_date, real_end_date)
    log(
        f"completed total_stocks={len(pending_stocks)} success={success} failed={failed} total_fetched={total_fetched} total_written={total_written} breadth_rows={breadth_rows} window={start_date}..{real_end_date}"
    )


if __name__ == "__main__":
    main()
