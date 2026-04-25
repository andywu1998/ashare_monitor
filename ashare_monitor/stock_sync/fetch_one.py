from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import execute_sql_file, upsert_stock_daily


TARGET_TS_CODE = "000001.SZ"
TARGET_NAME = "平安银行"
CHUNK_DAYS = 365 * 3


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
    end_date = date.today()
    cursor = date(1991, 4, 3)

    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        df = call_daily(pro, ts_code, cursor, chunk_end)
        if not df.empty:
            for row in df.itertuples(index=False):
                rows.append(
                    (
                        row.ts_code,
                        normalize_trade_date(row.trade_date),
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
