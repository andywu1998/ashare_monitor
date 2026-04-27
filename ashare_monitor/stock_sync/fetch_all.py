from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import (
    execute_sql_file,
    fetch_stock_basic_ts_codes,
    fetch_stock_basic_rows,
    upsert_stock_basic,
    upsert_stock_daily,
)


REQUEST_INTERVAL_SECONDS = 0.25
RATE_LIMIT_SLEEP_SECONDS = 65
MAX_RETRIES = 5
CHUNK_DAYS = 365 * 3
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


def call_with_retry(api_func, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return api_func(**kwargs)
        except Exception as exc:
            message = str(exc)
            if "频率超限" in message and attempt < MAX_RETRIES:
                print(
                    f"rate_limited attempt={attempt}/{MAX_RETRIES} sleep={RATE_LIMIT_SLEEP_SECONDS}s"
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
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
        print(f"moneyflow_fallback ts_code={ts_code} error={exc}")

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


def fetch_full_history_rows(pro, ts_code: str, list_date: date | None, end_date: date):
    effective_start = list_date or date(1990, 1, 1)
    rows = []
    cursor = effective_start

    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        rows.extend(fetch_daily_rows(pro, ts_code, cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_INTERVAL_SECONDS)

    return rows


def main():
    execute_sql_file("schema.sql")
    pro = init_pro()

    end_date = date.today()

    basic_ts_codes = fetch_stock_basic_ts_codes()
    if basic_ts_codes:
        print(f"stock_basic_loaded={len(basic_ts_codes)} source=mysql")
    else:
        basic_rows = fetch_stock_basic(pro)
        upsert_stock_basic(basic_rows)
        basic_ts_codes = [row[0] for row in basic_rows]
        print(f"stock_basic_written={len(basic_ts_codes)} source=tushare")

    stock_rows = fetch_stock_basic_rows()
    basic_ts_code_set = set(basic_ts_codes)
    pending_stocks = [
        (ts_code, name, list_date)
        for ts_code, name, list_date in stock_rows
        if ts_code in basic_ts_code_set
    ]
    print(f"daily_progress completed=0 pending={len(pending_stocks)} mode=full_history")

    success = 0
    failed = 0
    total_written = 0

    for index, (ts_code, name, list_date) in enumerate(pending_stocks, start=1):
        try:
            daily_rows = fetch_full_history_rows(pro, ts_code, list_date, end_date)
            written = upsert_stock_daily(daily_rows)
            total_written += written
            success += 1
            print(
                f"[{index}/{len(pending_stocks)}] ts_code={ts_code} name={name} list_date={list_date} rows_written={written}"
            )
        except Exception as exc:
            failed += 1
            print(f"[{index}/{len(pending_stocks)}] ts_code={ts_code} name={name} failed={exc}")

    print(
        f"completed total_stocks={len(pending_stocks)} success={success} failed={failed} total_daily_rows={total_written}"
    )


if __name__ == "__main__":
    main()
