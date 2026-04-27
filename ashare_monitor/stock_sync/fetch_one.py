from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import execute_sql_file, upsert_stock_daily


TARGET_TS_CODE = "000001.SZ"
TARGET_NAME = "平安银行"
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


def normalize_trade_date(value: str):
    return pd.to_datetime(value, format="%Y%m%d").date()


def call_daily(pro, ts_code: str, start_date: date, end_date: date):
    return pro.daily(
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )


def fetch_full_history_daily(ts_code: str):
    if not TUSHARE_TOKEN:
        raise ValueError("Missing TUSHARE_TOKEN")

    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    rows = []
    moneyflow_map = {}
    end_date = date.today()
    cursor = date(1991, 4, 3)

    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        mf_df = pro.moneyflow(
            ts_code=ts_code,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=chunk_end.strftime("%Y%m%d"),
        )
        if mf_df is not None and not mf_df.empty:
            for mf_row in mf_df.itertuples(index=False):
                trade_date = normalize_trade_date(getattr(mf_row, "trade_date"))
                moneyflow_map[trade_date] = {
                    field: getattr(mf_row, field, None) for field in MONEYFLOW_FIELD_NAMES
                }
        df = call_daily(pro, ts_code, cursor, chunk_end)
        if not df.empty:
            for row in df.itertuples(index=False):
                trade_date = normalize_trade_date(row.trade_date)
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
        cursor = chunk_end + timedelta(days=1)
    return rows


def main():
    execute_sql_file("schema.sql")
    rows = fetch_full_history_daily(TARGET_TS_CODE)
    inserted = upsert_stock_daily(rows)
    print(
        f"name={TARGET_NAME} ts_code={TARGET_TS_CODE} rows_fetched={len(rows)} rows_written={inserted}"
    )


if __name__ == "__main__":
    main()
