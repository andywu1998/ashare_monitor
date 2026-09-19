#!/usr/bin/env python3
"""港股逐日 PS(TTM) 序列抓取（默认 09660.HK 地平线机器人，自上市首日起）。

数据源
------
1. 日线行情            AkShare 新浪 `stock_hk_daily`（见 docs/hk_data_sources.md，本仓库港股主数据源）
2. 每日总市值(港元)    百度股市通 finance.baidu.com/opendata（query=总市值, market=hk, chart_select=全部）
3. 营业收入(人民币)    东方财富港股财务报表（AkShare stock_financial_hk_analysis_indicator_em / stock_financial_hk_report_em）
4. 业绩公告发布时点    港交所披露易 titleSearchServlet（官方，取业绩公告实际发布日）
5. 港元兑人民币汇率    中国银行港币牌价（AkShare currency_boc_sina，优先央行中间价）

计算口径
--------
    TTM营业收入(港元) = TTM营业收入(人民币) / HKDCNY
    PS(TTM)          = 当日总市值(港元) / TTM营业收入(港元)

    TTM 以“最近一期已公告”的定期报告为准：
      - 最新一期为年报 → TTM = 该年报营业额
      - 最新一期为中报 → TTM = 上年下半年 + 本年上半年（上年年报 - 上年中报 + 本年中报）
    业绩公告在收盘后（>=16:00）发布时，自下一个交易日开始生效。

用法
----
    ./.venv/bin/python scripts/run_hk_ps_series.py
    ./.venv/bin/python scripts/run_hk_ps_series.py --symbol 09660 --output reports/hk_ps_09660_daily.csv
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_SYMBOL = "09660"
DEFAULT_FX_RATE = 0.90
ANNUAL_LAG_DAYS = 80
INTERIM_LAG_DAYS = 62
AFTER_HOURS = (16, 0)

HEADERS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
    )
}
HEADERS_DESKTOP = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def log(message: str) -> None:
    print(message, flush=True)


def normalize_symbol(raw: str) -> str:
    text = str(raw).strip().upper().replace(".HK", "")
    text = re.sub(r"^HK", "", text)
    if not text.isdigit():
        raise ValueError(f"无法识别的港股代码: {raw}")
    return text.zfill(5)


def http_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = 3,
    sleep: float = 2.0,
    jsonp: bool = False,
):
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url, params=params, headers=headers or HEADERS_DESKTOP, timeout=30
            )
            resp.raise_for_status()
            text = resp.text.strip()
            if jsonp and not text.startswith(("{", "[")):
                text = text[text.index("(") + 1 : text.rindex(")")]
            return json.loads(text)
        except Exception as exc:  # noqa: BLE001 - 统一重试
            last_error = exc
            if attempt < retries:
                time.sleep(sleep * attempt)
    raise RuntimeError(f"请求失败 url={url} params={params} error={last_error}")


# --------------------------------------------------------------------------- #
# 1. 日线行情（新浪，港股主数据源）
# --------------------------------------------------------------------------- #
def fetch_daily_price(symbol: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_hk_daily(symbol=symbol, adjust="")
    df = df.rename(columns={"date": "trade_date", "close": "close_hkd"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["close_hkd"] = pd.to_numeric(df["close_hkd"], errors="coerce")
    df = df[["trade_date", "close_hkd"]].dropna().sort_values("trade_date")
    return df.drop_duplicates("trade_date").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 2. 每日总市值（百度股市通）
# --------------------------------------------------------------------------- #
def fetch_market_cap(symbol: str) -> pd.DataFrame:
    params = {
        "openapi": "1",
        "dspName": "iphone",
        "tn": "tangram",
        "client": "app",
        "query": "总市值",
        "code": symbol,
        "word": "",
        "resource_id": "51171",
        "market": "hk",
        "tag": "总市值",
        "chart_select": "全部",
        "industry_select": "",
        "skip_industry": "1",
        "finClientType": "pc",
    }
    # gushitong.baidu.com 会 301 到 finance.baidu.com，直连目标域名
    data = http_json(
        "https://finance.baidu.com/opendata", params=params, headers=HEADERS_MOBILE
    )
    body = data["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"][
        "chartInfo"
    ][0]["body"]
    df = pd.DataFrame(body, columns=["trade_date", "market_cap_100m_hkd"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["market_cap_hkd"] = pd.to_numeric(df["market_cap_100m_hkd"], errors="coerce") * 1e8
    df = df[["trade_date", "market_cap_hkd"]].dropna().sort_values("trade_date")
    return df.drop_duplicates("trade_date").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 3. 营业收入（东方财富港股财务报表）
# --------------------------------------------------------------------------- #
def fetch_revenue(symbol: str) -> pd.DataFrame:
    import akshare as ak

    frames: list[pd.DataFrame] = []

    def _collect(df: pd.DataFrame, col: str) -> None:
        if df is None or df.empty or col not in df.columns:
            return
        part = df[["REPORT_DATE", col]].rename(columns={col: "revenue_rmb"})
        frames.append(part)

    try:
        _collect(
            ak.stock_financial_hk_analysis_indicator_em(symbol=symbol, indicator="报告期"),
            "OPERATE_INCOME",
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 主要指标接口失败: {exc}")

    try:
        report = ak.stock_financial_hk_report_em(
            stock=symbol, symbol="利润表", indicator="报告期"
        )
        _collect(report[report["STD_ITEM_NAME"] == "营业额"], "AMOUNT")
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 利润表接口失败: {exc}")

    if not frames:
        raise RuntimeError(f"{symbol} 未能获取任何营业收入数据")

    df = pd.concat(frames, ignore_index=True)
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"]).dt.date
    df["revenue_rmb"] = pd.to_numeric(df["revenue_rmb"], errors="coerce")
    df = df.dropna().sort_values("REPORT_DATE")
    # 同一报告期以主要指标为主，利润表补齐缺失期
    df = df.drop_duplicates("REPORT_DATE", keep="first")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 4. 业绩公告发布时点（港交所披露易，官方）
# --------------------------------------------------------------------------- #
PERIOD_PATTERNS = [
    re.compile(r"YEAR ENDED\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I),
    re.compile(r"SIX MONTHS ENDED\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I),
    re.compile(r"NINE MONTHS ENDED\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I),
    re.compile(r"THREE MONTHS ENDED\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I),
]


def _parse_period_end(title: str) -> date | None:
    for pattern in PERIOD_PATTERNS:
        match = pattern.search(title)
        if match:
            month, day, year = match.groups()
            try:
                return datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date()
            except ValueError:
                return None
    return None


def _parse_hkex_datetime(raw: str) -> datetime | None:
    text = re.sub(r"\s+", " ", str(raw)).strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fetch_announcement_dates(symbol: str, start: date) -> dict[date, datetime]:
    code = symbol
    prefix = http_json(
        "https://www1.hkexnews.hk/search/prefix.do",
        params={"callback": "cb", "lang": "EN", "type": "A", "name": code, "market": "SEHK"},
        headers=HEADERS_DESKTOP,
        jsonp=True,
    )
    stock_info = prefix.get("stockInfo") or []
    if not stock_info:
        raise RuntimeError(f"港交所未找到股票代码 {code}")
    stock_id = stock_info[0]["stockId"]

    payload = http_json(
        "https://www1.hkexnews.hk/search/titleSearchServlet.do",
        params={
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "-1",
            "fromDate": start.strftime("%Y%m%d"),
            "toDate": datetime.now().strftime("%Y%m%d"),
            "title": "",
            "searchType": "1",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "0",
            "rowRange": "400",
            "lang": "EN",
        },
        headers=HEADERS_DESKTOP,
    )
    rows = json.loads(payload.get("result") or "[]") if isinstance(payload, dict) else payload
    ann_map: dict[date, datetime] = {}
    for row in rows:
        title = re.sub(r"\s+", " ", str(row.get("TITLE", "")))
        if "RESULTS ANNOUNCEMENT" not in title.upper():
            continue
        period_end = _parse_period_end(title)
        announced_at = _parse_hkex_datetime(row.get("DATE_TIME", ""))
        if not period_end or not announced_at:
            continue
        current = ann_map.get(period_end)
        if current is None or announced_at < current:
            ann_map[period_end] = announced_at
    return ann_map


# --------------------------------------------------------------------------- #
# 5. 港元兑人民币汇率（中国银行牌价）
# --------------------------------------------------------------------------- #
def fetch_hkd_cny_fx(start: date, end: date, fallback: float) -> pd.Series:
    import akshare as ak

    try:
        df = ak.currency_boc_sina(
            symbol="港币",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        df["日期"] = pd.to_datetime(df["日期"]).dt.date
        rate = df["央行中间价"].fillna(df["中行折算价"])
        rate = pd.to_numeric(rate, errors="coerce") / 100.0
        series = pd.Series(rate.values, index=df["日期"].values).dropna()
        if not series.empty:
            return series.sort_index()
        log("[warn] 中行牌价为空，使用固定汇率")
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 中行牌价获取失败({exc})，使用固定汇率 {fallback}")
    return pd.Series(dtype="float64")


# --------------------------------------------------------------------------- #
# 组合计算
# --------------------------------------------------------------------------- #
def build_ttm_steps(
    revenue: pd.DataFrame, announcements: dict[date, datetime]
) -> list[tuple[date, float, str]]:
    """返回 [(生效日期, TTM营收(人民币), 口径标签), ...]，按生效日期升序。"""
    fy: dict[int, float] = {}
    h1: dict[int, float] = {}
    for row in revenue.itertuples(index=False):
        if row.REPORT_DATE.month == 12:
            fy[row.REPORT_DATE.year] = row.revenue_rmb
        elif row.REPORT_DATE.month == 6:
            h1[row.REPORT_DATE.year] = row.revenue_rmb

    steps: list[tuple[date, float, str]] = []
    for period_end in sorted(revenue["REPORT_DATE"]):
        if period_end.month == 12:
            ttm = fy.get(period_end.year)
            label = f"FY{period_end.year}"
            default_lag = ANNUAL_LAG_DAYS
        elif period_end.month == 6:
            year = period_end.year
            prev_fy, prev_h1, cur_h1 = fy.get(year - 1), h1.get(year - 1), h1.get(year)
            if prev_fy is None or prev_h1 is None or cur_h1 is None:
                continue
            ttm = prev_fy - prev_h1 + cur_h1
            label = f"TTM@{period_end:%Y-%m} (H2{year - 1}+H1{year})"
            default_lag = INTERIM_LAG_DAYS
        else:
            # 季度/其他报告期不参与 TTM 口径（港股主板公司通常只披露半年报与年报）
            continue
        if ttm is None:
            continue

        announced_at = announcements.get(period_end)
        if announced_at is None:
            announced_at = datetime.combine(
                period_end + timedelta(days=default_lag), datetime.min.time()
            )
        steps.append((announced_at, ttm, label, period_end))

    steps.sort(key=lambda item: item[0])
    return steps


def resolve_effective_dates(steps, trading_days: list[date]):
    """公告在收盘后发布 → 顺延到下一个交易日生效。"""
    resolved = []
    for announced_at, ttm, label, period_end in steps:
        effective = announced_at.date()
        if (announced_at.hour, announced_at.minute) >= AFTER_HOURS:
            later = [d for d in trading_days if d > effective]
            effective = later[0] if later else effective
        resolved.append((effective, ttm, label))
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="港股逐日 PS(TTM) 序列抓取")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="港股代码，默认 09660")
    parser.add_argument("--output", default=None, help="输出 CSV 路径")
    parser.add_argument("--cache-dir", default=str(ROOT_DIR / "data" / "hk_ps_cache"))
    parser.add_argument("--fx-rate", type=float, default=DEFAULT_FX_RATE, help="汇率兜底值")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（默认取上市首日）")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    symbol = normalize_symbol(args.symbol)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else ROOT_DIR / "reports" / f"hk_ps_{symbol}_daily.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    log(f"[1/5] 抓取日线行情（AkShare 新浪） symbol={symbol}")
    price = fetch_daily_price(symbol)
    end_date = date.fromisoformat(args.end) if args.end else price["trade_date"].max()
    start_date = date.fromisoformat(args.start) if args.start else price["trade_date"].min()
    price = price[(price["trade_date"] >= start_date) & (price["trade_date"] <= end_date)]
    trading_days = list(price["trade_date"])
    log(f"      交易日 {len(trading_days)} 天: {trading_days[0]} ~ {trading_days[-1]}")

    log("[2/5] 抓取每日总市值（百度股市通）")
    market_cap = fetch_market_cap(symbol)
    market_cap = market_cap[
        (market_cap["trade_date"] >= start_date) & (market_cap["trade_date"] <= end_date)
    ]
    log(f"      市值序列 {len(market_cap)} 天: {market_cap['trade_date'].min()} ~ {market_cap['trade_date'].max()}")

    log("[3/5] 抓取营业收入（东方财富港股财报）")
    revenue = fetch_revenue(symbol)
    recent = revenue.tail(8)
    prefix = "..." if len(revenue) > len(recent) else ""
    log("      报告期营业额(人民币千元): " + prefix + ", ".join(
        f"{r.REPORT_DATE}:{r.revenue_rmb/1000:,.0f}" for r in recent.itertuples(index=False)
    ))

    log("[4/5] 抓取业绩公告发布时点（港交所披露易）")
    try:
        announcements = fetch_announcement_dates(symbol, start_date - timedelta(days=400))
        for period_end, announced_at in sorted(announcements.items()):
            log(f"      {period_end} 业绩 -> {announced_at:%Y-%m-%d %H:%M} 公告")
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 公告日期获取失败({exc})，使用 年报+80天 / 中报+62天 兜底")
        announcements = {}

    log("[5/5] 抓取港元兑人民币汇率（中国银行）")
    fx = fetch_hkd_cny_fx(start_date, end_date, args.fx_rate)
    if fx.empty:
        log(f"      使用固定汇率 {args.fx_rate}")

    steps = build_ttm_steps(revenue, announcements)
    steps = [s for s in steps if s[0].date() <= end_date]
    if not steps:
        raise RuntimeError("没有可用的 TTM 营收口径")
    steps = resolve_effective_dates(steps, trading_days)
    for effective, ttm, label in steps:
        log(f"      {effective} 起 TTM营收 = {ttm/1e6:,.1f} 百万人民币  [{label}]")

    # 首个生效日之前的交易日沿用最早口径
    effective_dates = [s[0] for s in steps]
    ttm_values = [s[1] for s in steps]
    labels = [s[2] for s in steps]

    rows = []
    for day in trading_days:
        idx = bisect.bisect_right(effective_dates, day) - 1
        if idx < 0:
            idx = 0
        rows.append(
            {
                "trade_date": day,
                "ttm_revenue_rmb": ttm_values[idx],
                "ttm_basis": labels[idx],
            }
        )
    result = pd.DataFrame(rows)
    result = result.merge(price, on="trade_date", how="left")
    result = result.merge(market_cap, on="trade_date", how="left")
    result["market_cap_hkd"] = result["market_cap_hkd"].ffill()
    missing_cap = result["market_cap_hkd"].isna()
    if missing_cap.any():
        log(
            f"[warn] 剔除 {int(missing_cap.sum())} 个交易日：百度市值序列 "
            f"{market_cap['trade_date'].min()} 起才有数据"
        )
        result = result[~missing_cap].reset_index(drop=True)
    result["shares_implied"] = result["market_cap_hkd"] / result["close_hkd"]

    if fx.empty:
        result["hkd_cny"] = args.fx_rate
    else:
        fx_map = fx.to_dict()
        result["hkd_cny"] = result["trade_date"].map(fx_map).ffill()
        if result["hkd_cny"].isna().any():
            result["hkd_cny"] = result["hkd_cny"].fillna(args.fx_rate)

    result["ttm_revenue_hkd"] = result["ttm_revenue_rmb"] / result["hkd_cny"]
    result["ps_ttm"] = result["market_cap_hkd"] / result["ttm_revenue_hkd"]

    columns = [
        "trade_date",
        "close_hkd",
        "market_cap_hkd",
        "shares_implied",
        "ps_ttm",
        "ttm_revenue_rmb",
        "ttm_revenue_hkd",
        "hkd_cny",
        "ttm_basis",
    ]
    result = result[columns].sort_values("trade_date").reset_index(drop=True)
    result.to_csv(output, index=False)

    # 原始数据留档，便于复核
    price.to_csv(cache_dir / f"{symbol}_price_sina.csv", index=False)
    market_cap.to_csv(cache_dir / f"{symbol}_market_cap_baidu.csv", index=False)
    revenue.to_csv(cache_dir / f"{symbol}_revenue_em.csv", index=False)
    if not fx.empty:
        fx.rename("hkd_cny").to_csv(cache_dir / f"{symbol}_hkd_cny_boc.csv")
    (cache_dir / f"{symbol}_ttm_steps.json").write_text(
        json.dumps(
            [
                {"effective_date": str(e), "ttm_revenue_rmb": t, "basis": l}
                for e, t, l in steps
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("")
    log(f"输出: {output}  共 {len(result)} 个交易日")
    log("")
    log("各报告期口径下的 PS:")
    for effective, _ttm, label in steps:
        window = result[result["trade_date"] >= effective]
        window = window[window["ttm_basis"] == label]
        if window.empty:
            continue
        log(
            f"  {label:<28} {effective} 起  "
            f"PS 区间 {window['ps_ttm'].min():.2f} ~ {window['ps_ttm'].max():.2f}, "
            f"区间末 {window['ps_ttm'].iloc[-1]:.2f}"
        )
    head, tail = result.head(3), result.tail(3)
    log("")
    log("首尾样本:")
    for frame in (head, tail):
        for row in frame.itertuples(index=False):
            log(
                f"  {row.trade_date}  收盘 {row.close_hkd:.2f} HKD  "
                f"市值 {row.market_cap_hkd/1e8:,.2f} 亿港元  "
                f"隐含股本 {row.shares_implied/1e9:.3f} 十亿股  "
                f"PS(TTM) {row.ps_ttm:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
