#!/usr/bin/env python3
"""Probe multiple HK market data sources and print availability report."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.stock_sync.fetch_all_hk_concurrent import (
    PROVIDER_AKSHARE_EM,
    PROVIDER_AKSHARE_SINA,
    PROVIDER_TUSHARE,
    PROVIDER_YFINANCE,
    SUPPORTED_PROVIDERS,
    fetch_hk_daily_rows_akshare_em,
    fetch_hk_daily_rows_akshare_sina,
    fetch_hk_daily_rows_tushare,
    fetch_hk_daily_rows_yfinance,
)


FETCHERS = {
    PROVIDER_TUSHARE: fetch_hk_daily_rows_tushare,
    PROVIDER_AKSHARE_EM: fetch_hk_daily_rows_akshare_em,
    PROVIDER_AKSHARE_SINA: fetch_hk_daily_rows_akshare_sina,
    PROVIDER_YFINANCE: fetch_hk_daily_rows_yfinance,
}


def run_probe(ts_code: str, days: int, providers: list[str]) -> pd.DataFrame:
    end_date = date.today()
    start_date = max(date(1990, 1, 1), end_date - timedelta(days=days))
    rows = []
    for provider in providers:
        fetcher = FETCHERS[provider]
        try:
            data = fetcher(ts_code=ts_code, start_date=start_date, end_date=end_date)
            rows.append(
                {
                    "provider": provider,
                    "status": "ok",
                    "rows": len(data),
                    "detail": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "provider": provider,
                    "status": "failed",
                    "rows": 0,
                    "detail": str(exc),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe HK data sources")
    parser.add_argument("--ts-code", default="00005.HK", help="HK ts_code, e.g. 00005.HK")
    parser.add_argument("--days", type=int, default=365, help="Lookback days for probe")
    parser.add_argument(
        "--providers",
        nargs="*",
        default=SUPPORTED_PROVIDERS,
        help="Providers to probe",
    )
    args = parser.parse_args()

    df = run_probe(args.ts_code, args.days, args.providers)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
