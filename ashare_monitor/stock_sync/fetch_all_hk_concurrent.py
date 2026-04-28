from __future__ import annotations

import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable

import akshare as ak
import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN
from .db import execute_sql_file, get_connection, upsert_stock_basic, upsert_stock_daily


DEFAULT_START_DATE = date(1990, 1, 1)
REQUEST_INTERVAL_SECONDS = float(os.getenv("HK_REQUEST_INTERVAL_SECONDS", "0.2"))
RATE_LIMIT_SLEEP_SECONDS = float(os.getenv("HK_RATE_LIMIT_SLEEP_SECONDS", "12"))
FAILURE_RETRY_SLEEP_SECONDS = float(os.getenv("HK_FAILURE_RETRY_SECONDS", "3"))
MAX_API_RETRIES = 4

PRINT_LOCK = threading.Lock()
THREAD_LOCAL = threading.local()
RATE_LIMIT_LOCK = threading.Lock()
LAST_REQUEST_TS = 0.0
STOP_EVENT = threading.Event()

PROVIDER_TUSHARE = "tushare"
PROVIDER_AKSHARE_EM = "akshare_em"
PROVIDER_AKSHARE_SINA = "akshare_sina"
PROVIDER_YFINANCE = "yfinance"
SUPPORTED_PROVIDERS = [
    PROVIDER_AKSHARE_SINA,
    PROVIDER_AKSHARE_EM,
    PROVIDER_TUSHARE,
    PROVIDER_YFINANCE,
]


class ProviderFatalError(Exception):
    pass


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def parse_date(value):
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() == "nat":
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return pd.to_datetime(text, errors="coerce").date() if pd.notna(pd.to_datetime(text, errors="coerce")) else None


def ts_code_to_symbol(ts_code: str) -> str:
    return str(ts_code).split(".")[0].zfill(5)


def symbol_to_ts_code(symbol: str) -> str:
    return f"{str(symbol).zfill(5)}.HK"


def wait_request_slot() -> None:
    global LAST_REQUEST_TS
    with RATE_LIMIT_LOCK:
        now = time.time()
        wait = REQUEST_INTERVAL_SECONDS - (now - LAST_REQUEST_TS)
        if wait > 0:
            time.sleep(wait)
        LAST_REQUEST_TS = time.time()


def call_with_retry(api_func: Callable, provider_name: str, **kwargs):
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            wait_request_slot()
            return api_func(**kwargs)
        except Exception as exc:
            msg = str(exc)
            lower_msg = msg.lower()
            if provider_name == PROVIDER_TUSHARE and "10次/天" in msg:
                raise ProviderFatalError(msg) from exc
            if ("频率超限" in msg or "too many requests" in lower_msg) and attempt < MAX_API_RETRIES:
                log(
                    f"rate_limited provider={provider_name} attempt={attempt}/{MAX_API_RETRIES} "
                    f"sleep={RATE_LIMIT_SLEEP_SECONDS}s kwargs={kwargs}"
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            if attempt < MAX_API_RETRIES:
                log(
                    f"api_retry provider={provider_name} attempt={attempt}/{MAX_API_RETRIES} "
                    f"sleep={FAILURE_RETRY_SLEEP_SECONDS}s error={exc} kwargs={kwargs}"
                )
                time.sleep(FAILURE_RETRY_SLEEP_SECONDS)
                continue
            raise


def init_tushare_pro():
    if not TUSHARE_TOKEN:
        raise ValueError("Missing TUSHARE_TOKEN")
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()


def get_thread_tushare_pro():
    pro = getattr(THREAD_LOCAL, "tushare_pro", None)
    if pro is None:
        pro = init_tushare_pro()
        THREAD_LOCAL.tushare_pro = pro
    return pro


def rows_from_kline_df(ts_code: str, df: pd.DataFrame) -> list[tuple]:
    if df is None or df.empty:
        return []
    parsed = []
    for row in df.itertuples(index=False):
        trade_date = parse_date(
            getattr(row, "trade_date", None)
            or getattr(row, "日期", None)
            or getattr(row, "date", None)
        )
        if trade_date is None:
            continue
        parsed.append(
            {
                "trade_date": trade_date,
                "open_px": pd.to_numeric(
                    getattr(row, "open", None) or getattr(row, "开盘", None),
                    errors="coerce",
                ),
                "high_px": pd.to_numeric(
                    getattr(row, "high", None) or getattr(row, "最高", None),
                    errors="coerce",
                ),
                "low_px": pd.to_numeric(
                    getattr(row, "low", None) or getattr(row, "最低", None),
                    errors="coerce",
                ),
                "close_px": pd.to_numeric(
                    getattr(row, "close", None) or getattr(row, "收盘", None),
                    errors="coerce",
                ),
                "vol": pd.to_numeric(
                    getattr(row, "vol", None)
                    or getattr(row, "volume", None)
                    or getattr(row, "成交量", None),
                    errors="coerce",
                ),
                "amount": pd.to_numeric(
                    getattr(row, "amount", None) or getattr(row, "成交额", None),
                    errors="coerce",
                ),
                "pre_close": pd.to_numeric(
                    getattr(row, "pre_close", None), errors="coerce"
                ),
                "change": pd.to_numeric(
                    getattr(row, "change", None) or getattr(row, "涨跌额", None),
                    errors="coerce",
                ),
                "pct_chg": pd.to_numeric(
                    getattr(row, "pct_chg", None) or getattr(row, "涨跌幅", None),
                    errors="coerce",
                ),
            }
        )

    parsed.sort(key=lambda x: x["trade_date"])

    rows = []
    prev_close_px = None
    for item in parsed:
        trade_date = item["trade_date"]
        open_px = item["open_px"]
        high_px = item["high_px"]
        low_px = item["low_px"]
        close_px = item["close_px"]
        vol = item["vol"]
        amount = item["amount"]
        pre_close = item["pre_close"]
        change = item["change"]
        pct_chg = item["pct_chg"]

        if pd.notna(close_px) and pd.isna(pre_close):
            if pd.notna(change):
                pre_close = close_px - change
            elif prev_close_px is not None:
                pre_close = prev_close_px
        if pd.notna(close_px) and pd.notna(pre_close) and pd.isna(change):
            change = close_px - pre_close
        if (
            pd.notna(close_px)
            and pd.notna(pre_close)
            and pre_close not in (0, 0.0)
            and pd.isna(pct_chg)
        ):
            pct_chg = (close_px / pre_close - 1.0) * 100

        rows.append(
            (
                ts_code,
                trade_date,
                "H",
                None if pd.isna(open_px) else float(open_px),
                None if pd.isna(high_px) else float(high_px),
                None if pd.isna(low_px) else float(low_px),
                None if pd.isna(close_px) else float(close_px),
                None if pd.isna(pre_close) else float(pre_close),
                None if pd.isna(change) else float(change),
                None if pd.isna(pct_chg) else float(pct_chg),
                None if pd.isna(vol) else float(vol),
                None if pd.isna(amount) else float(amount),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )

        if pd.notna(close_px):
            prev_close_px = float(close_px)

    return rows


def fetch_hk_basic_rows_tushare() -> list[tuple]:
    pro = init_tushare_pro()
    df = call_with_retry(
        pro.hk_basic,
        provider_name=PROVIDER_TUSHARE,
        fields="ts_code,name,list_date,delist_date,list_status,market",
    )
    if df is None or df.empty:
        return []
    rows = []
    for row in df.itertuples(index=False):
        ts_code = str(getattr(row, "ts_code", "")).strip()
        if not ts_code:
            continue
        symbol = ts_code_to_symbol(ts_code)
        rows.append(
            (
                ts_code,
                "H",
                symbol,
                getattr(row, "name", None),
                "HK",
                None,
                getattr(row, "market", None) or "HK",
                "HKEX",
                getattr(row, "list_status", "L") or "L",
                parse_date(getattr(row, "list_date", None)),
                parse_date(getattr(row, "delist_date", None)),
                None,
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


def fetch_hk_basic_rows_akshare() -> list[tuple]:
    df = call_with_retry(ak.stock_hk_spot, provider_name=PROVIDER_AKSHARE_SINA)
    if df is None or df.empty:
        return []
    rows = []
    seen = set()
    for row in df.itertuples(index=False):
        symbol = str(getattr(row, "代码", "")).strip().zfill(5)
        if not symbol:
            continue
        ts_code = symbol_to_ts_code(symbol)
        if ts_code in seen:
            continue
        seen.add(ts_code)
        rows.append(
            (
                ts_code,
                "H",
                symbol,
                getattr(row, "中文名称", None),
                "HK",
                None,
                "HK",
                "HKEX",
                "L",
                None,
                None,
                None,
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


def fetch_hk_daily_rows_tushare(ts_code: str, start_date: date, end_date: date) -> list[tuple]:
    pro = get_thread_tushare_pro()
    df = call_with_retry(
        pro.hk_daily,
        provider_name=PROVIDER_TUSHARE,
        ts_code=ts_code,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    return rows_from_kline_df(ts_code, df)


def fetch_hk_daily_rows_akshare_em(ts_code: str, start_date: date, end_date: date) -> list[tuple]:
    symbol = ts_code_to_symbol(ts_code)
    df = call_with_retry(
        ak.stock_hk_hist,
        provider_name=PROVIDER_AKSHARE_EM,
        symbol=symbol,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust="",
    )
    return rows_from_kline_df(ts_code, df)


def fetch_hk_daily_rows_akshare_sina(ts_code: str, start_date: date, end_date: date) -> list[tuple]:
    symbol = ts_code_to_symbol(ts_code)
    df = call_with_retry(
        ak.stock_hk_daily,
        provider_name=PROVIDER_AKSHARE_SINA,
        symbol=symbol,
        adjust="",
    )
    if df is None or df.empty:
        return []
    if "date" in df.columns:
        dt_index = pd.to_datetime(df["date"], errors="coerce")
        mask = (dt_index.dt.date >= start_date) & (dt_index.dt.date <= end_date)
        df = df.loc[mask].copy()
    return rows_from_kline_df(ts_code, df)


def fetch_hk_daily_rows_yfinance(ts_code: str, start_date: date, end_date: date) -> list[tuple]:
    import yfinance as yf

    symbol = ts_code_to_symbol(ts_code)
    yahoo_symbol = f"{symbol.lstrip('0').zfill(4)}.HK"

    def _get_hist():
        ticker = yf.Ticker(yahoo_symbol)
        return ticker.history(start=start_date.isoformat(), end=(end_date + timedelta(days=1)).isoformat(), auto_adjust=False, actions=False)

    df = call_with_retry(_get_hist, provider_name=PROVIDER_YFINANCE)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    df.rename(
        columns={
            "Date": "trade_date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
        inplace=True,
    )
    return rows_from_kline_df(ts_code, df)


PROVIDER_FETCHERS = {
    PROVIDER_TUSHARE: fetch_hk_daily_rows_tushare,
    PROVIDER_AKSHARE_EM: fetch_hk_daily_rows_akshare_em,
    PROVIDER_AKSHARE_SINA: fetch_hk_daily_rows_akshare_sina,
    PROVIDER_YFINANCE: fetch_hk_daily_rows_yfinance,
}


def fetch_all_hk_with_last_trade() -> list[tuple[str, str, object, object, int]]:
    sql = """
    SELECT
      b.ts_code,
      b.name,
      b.list_date,
      MAX(d.trade_date) AS last_trade_date,
      COUNT(d.trade_date) AS daily_rows
    FROM stock_basic b
    LEFT JOIN stock_daily d ON b.ts_code = d.ts_code AND d.asset_type = 'H'
    WHERE b.asset_type = 'H'
      AND b.list_status = 'L'
    GROUP BY b.ts_code, b.name, b.list_date
    ORDER BY CASE WHEN COUNT(d.trade_date) = 0 THEN 0 ELSE 1 END, b.ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def probe_provider(provider: str, ts_code: str, start_date: date, end_date: date) -> tuple[bool, str]:
    fetcher = PROVIDER_FETCHERS[provider]
    try:
        rows = fetcher(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if rows:
            return True, f"rows={len(rows)}"
        return True, "rows=0"
    except Exception as exc:
        return False, str(exc)


def choose_providers(provider_arg: str, stocks: list[tuple[str, str, object, object, int]]) -> list[str]:
    if provider_arg != "auto":
        return [provider_arg]
    sample_ts_code = stocks[0][0] if stocks else "00001.HK"
    probe_end = date.today()
    probe_start = max(DEFAULT_START_DATE, probe_end - timedelta(days=30))
    selected = []
    for provider in SUPPORTED_PROVIDERS:
        ok, detail = probe_provider(provider, sample_ts_code, probe_start, probe_end)
        if ok:
            log(f"provider_probe provider={provider} status=ok detail={detail}")
            selected.append(provider)
        else:
            log(f"provider_probe provider={provider} status=failed detail={detail}")
    if not selected:
        raise RuntimeError("No HK provider available in current network/token environment")
    return selected


def fetch_daily_with_provider_chain(ts_code: str, start_date: date, end_date: date, providers: list[str]) -> tuple[list[tuple], str]:
    last_exc = None
    for provider in providers:
        fetcher = PROVIDER_FETCHERS[provider]
        try:
            rows = fetcher(ts_code=ts_code, start_date=start_date, end_date=end_date)
            return rows, provider
        except ProviderFatalError:
            raise
        except Exception as exc:
            last_exc = exc
            log(f"provider_failed ts_code={ts_code} provider={provider} error={exc}")
            continue
    if last_exc is not None:
        raise last_exc
    return [], providers[0]


def process_hk_stock(
    index: int,
    total: int,
    ts_code: str,
    name: str,
    list_date: date | None,
    last_trade_date: date | None,
    end_date: date,
    full_refresh: bool,
    providers: list[str],
):
    if STOP_EVENT.is_set():
        return {"status": "stopped", "written": 0}
    try:
        start_date = list_date or DEFAULT_START_DATE
        if not full_refresh and last_trade_date:
            start_date = max(start_date, last_trade_date + timedelta(days=1))
        if start_date > end_date:
            log(f"[{index}/{total}] hk={ts_code} name={name} skipped up_to_date")
            return {"status": "skipped", "written": 0}

        rows, provider = fetch_daily_with_provider_chain(ts_code, start_date, end_date, providers)
        written = upsert_stock_daily(rows) if rows else 0
        log(
            f"[{index}/{total}] hk={ts_code} name={name} provider={provider} "
            f"start={start_date} end={end_date} rows={len(rows)} written={written}"
        )
        return {"status": "success", "written": written}
    except ProviderFatalError as exc:
        STOP_EVENT.set()
        log(f"[{index}/{total}] hk={ts_code} name={name} fatal={exc}")
        return {"status": "fatal", "written": 0}
    except Exception as exc:
        log(f"[{index}/{total}] hk={ts_code} name={name} failed={exc}")
        return {"status": "failed", "written": 0}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch all HK stock basic + daily data into stock_basic/stock_daily."
    )
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--max-stocks", type=int, default=0, help="Debug limit, 0 means all.")
    parser.add_argument("--full-refresh", action="store_true", help="Ignore last_trade_date and refetch all history.")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only fetch symbols with no HK daily rows yet (best for first full backfill).",
    )
    parser.add_argument(
        "--provider",
        choices=["auto"] + SUPPORTED_PROVIDERS,
        default="auto",
        help="Data provider: auto tries providers one by one and keeps available chain.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=REQUEST_INTERVAL_SECONDS,
        help="Global min seconds between API calls.",
    )
    parser.add_argument(
        "--rate-limit-sleep",
        type=float,
        default=RATE_LIMIT_SLEEP_SECONDS,
        help="Sleep seconds when provider returns rate limit.",
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

    global REQUEST_INTERVAL_SECONDS, RATE_LIMIT_SLEEP_SECONDS, FAILURE_RETRY_SLEEP_SECONDS
    REQUEST_INTERVAL_SECONDS = float(args.request_interval)
    RATE_LIMIT_SLEEP_SECONDS = float(args.rate_limit_sleep)
    FAILURE_RETRY_SLEEP_SECONDS = float(args.retry_sleep)

    execute_sql_file("schema.sql")

    basic_rows = []
    basic_source = "tushare.hk_basic"
    try:
        basic_rows = fetch_hk_basic_rows_tushare()
    except Exception as exc:
        log(f"hk_basic_tushare_failed error={exc}; fallback=akshare.stock_hk_spot")
        basic_rows = fetch_hk_basic_rows_akshare()
        basic_source = "akshare.stock_hk_spot"
    upsert_stock_basic(basic_rows)
    log(f"hk_basic_loaded={len(basic_rows)} source={basic_source}")
    log(
        "runtime_config "
        f"concurrency={args.concurrency} "
        f"provider={args.provider} "
        f"request_interval={REQUEST_INTERVAL_SECONDS}s "
        f"rate_limit_sleep={RATE_LIMIT_SLEEP_SECONDS}s "
        f"retry_sleep={FAILURE_RETRY_SLEEP_SECONDS}s "
        f"full_refresh={args.full_refresh} "
        f"missing_only={args.missing_only}"
    )

    end_date = date.today()
    stocks = fetch_all_hk_with_last_trade()
    if args.missing_only:
        stocks = [item for item in stocks if int(item[4] or 0) == 0]
    if args.max_stocks > 0:
        stocks = stocks[: args.max_stocks]
    log(f"hk_stocks_total={len(stocks)}")

    providers = choose_providers(args.provider, stocks)
    log(f"provider_chain={providers}")

    success = 0
    failed = 0
    skipped = 0
    stopped = 0
    fatal = 0
    total_written = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                process_hk_stock,
                index,
                len(stocks),
                ts_code,
                name,
                list_date,
                last_trade_date,
                end_date,
                args.full_refresh,
                providers,
            )
            for index, (ts_code, name, list_date, last_trade_date, _daily_rows) in enumerate(stocks, start=1)
        ]
        for future in as_completed(futures):
            result = future.result()
            total_written += int(result.get("written", 0))
            status = result.get("status")
            if status == "success":
                success += 1
            elif status == "skipped":
                skipped += 1
            elif status == "fatal":
                fatal += 1
                for pending in futures:
                    pending.cancel()
                break
            elif status == "stopped":
                stopped += 1
            else:
                failed += 1
    log(
        f"completed hk_total={len(stocks)} success={success} skipped={skipped} failed={failed} "
        f"stopped={stopped} fatal={fatal} total_rows={total_written}"
    )


if __name__ == "__main__":
    main()
