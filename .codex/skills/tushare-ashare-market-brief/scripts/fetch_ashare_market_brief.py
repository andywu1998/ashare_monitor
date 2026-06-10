#!/usr/bin/env python3
"""Fetch a structured A-share market brief data package with Tushare."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

INDEX_CODES = {
    "上证指数": "000001.SH",
    "深证成指": "399001.SZ",
    "创业板指": "399006.SZ",
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "中证1000": "000852.SH",
    "科创50": "000688.SH",
}


def load_env_from_zshrc() -> None:
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    try:
        proc = subprocess.run(
            ["zsh", "-lc", f"source {shlex.quote(str(zshrc))} >/dev/null 2>&1; env -0"],
            check=True,
            capture_output=True,
        )
    except Exception:
        proc = None
    if proc is not None:
        for chunk in proc.stdout.split(b"\x00"):
            if not chunk or b"=" not in chunk:
                continue
            key, value = chunk.split(b"=", 1)
            key_text = key.decode("utf-8", errors="ignore")
            if key_text and key_text not in os.environ:
                os.environ[key_text] = value.decode("utf-8", errors="ignore")
    # Fallback for environments without zsh: parse simple export lines.
    for raw_line in zshrc.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[len("export "):].split("=", 1)
        key = key.strip()
        if key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def init_pro():
    load_env_from_zshrc()
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TUSHARE_TOKEN")
    try:
        import tushare as ts
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing tushare package. Install project requirements first.") from exc
    ts.set_token(token)
    return ts.pro_api()


def call_api(func, retries: int = 4, sleep_seconds: float = 1.0, **kwargs) -> pd.DataFrame:
    for attempt in range(1, retries + 1):
        try:
            df = func(**kwargs)
            return df if df is not None else pd.DataFrame()
        except Exception as exc:
            msg = str(exc)
            if attempt < retries and ("频率超限" in msg or "timeout" in msg.lower()):
                time.sleep(sleep_seconds * attempt)
                continue
            raise
    return pd.DataFrame()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def resolve_trade_date(pro, requested: str) -> tuple[str, str | None]:
    today = date.today()
    end = ymd(today + timedelta(days=1))
    start = ymd(today - timedelta(days=90))
    cal = call_api(pro.trade_cal, exchange="SSE", start_date=start, end_date=end, fields="cal_date,is_open,pretrade_date")
    if cal.empty:
        raise RuntimeError("trade_cal returned empty")
    cal = cal.sort_values("cal_date")
    open_days = cal[cal["is_open"].astype(int) == 1]["cal_date"].astype(str).tolist()
    if not open_days:
        raise RuntimeError("no open trade date found")
    if requested == "latest":
        # Tushare calendar can expose the next open day before stock daily data lands.
        # Use the latest open day that already has stock daily rows.
        for candidate in reversed(open_days):
            try:
                sample = call_api(pro.daily, trade_date=candidate)
            except Exception:
                sample = pd.DataFrame()
            if not sample.empty:
                return candidate, None
        raise RuntimeError("no open trade date with daily rows found")
    req = requested.replace("-", "")
    if req in open_days:
        return req, None
    previous = [d for d in open_days if d <= req]
    if not previous:
        raise RuntimeError(f"no previous open trade date for {requested}")
    return previous[-1], req


def recent_trade_dates(pro, trade_date: str, lookback: int) -> list[str]:
    dt = datetime.strptime(trade_date, "%Y%m%d").date()
    cal = call_api(
        pro.trade_cal,
        exchange="SSE",
        start_date=ymd(dt - timedelta(days=lookback * 3 + 30)),
        end_date=trade_date,
        fields="cal_date,is_open",
    )
    days = cal[cal["is_open"].astype(int) == 1].sort_values("cal_date")["cal_date"].astype(str).tolist()
    return days[-lookback:]


def df_records(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    if limit is not None:
        df = df.head(limit)
    cleaned = df.copy()
    cleaned = cleaned.where(pd.notnull(cleaned), None)
    return json.loads(cleaned.to_json(orient="records", force_ascii=False))


def amount_yi(amount_qianyuan: Any) -> float | None:
    try:
        return round(float(amount_qianyuan) / 100000, 2)
    except Exception:
        return None


def classify_position(hist: pd.DataFrame) -> str:
    if hist.empty or len(hist) < 5:
        return "样本不足"
    hist = hist.sort_values("trade_date")
    latest = hist.iloc[-1]
    close = float(latest["close"])
    high30 = float(hist["high"].max())
    low30 = float(hist["low"].min())
    pct30 = pct_change(hist, min(30, len(hist)))
    pct5 = pct_change(hist, min(5, len(hist)))
    amount_ratio = latest_amount_ratio(hist)
    near_high = high30 > 0 and close >= high30 * 0.97
    near_low = low30 > 0 and close <= low30 * 1.08
    if near_high and amount_ratio and amount_ratio >= 1.8 and pct5 is not None and pct5 > 8:
        return "高位放量加速/分歧"
    if near_high and pct30 is not None and pct30 > 15:
        return "高位强趋势"
    if near_low and pct5 is not None and pct5 > 3:
        return "低位反弹"
    if pct30 is not None and pct30 > 10 and pct5 is not None and pct5 < 0:
        return "上升后回调"
    if pct30 is not None and abs(pct30) < 5:
        return "区间震荡"
    if pct30 is not None and pct30 < -10:
        return "弱势下行"
    return "中位运行"


def pct_change(hist: pd.DataFrame, periods: int) -> float | None:
    if hist.empty or len(hist) < 2:
        return None
    hist = hist.sort_values("trade_date")
    if len(hist) >= periods:
        base = float(hist.iloc[-periods]["close"])
    else:
        base = float(hist.iloc[0]["close"])
    latest = float(hist.iloc[-1]["close"])
    if base == 0:
        return None
    return round((latest / base - 1) * 100, 2)


def latest_amount_ratio(hist: pd.DataFrame) -> float | None:
    if hist.empty or "amount" not in hist:
        return None
    latest = float(hist.sort_values("trade_date").iloc[-1]["amount"])
    avg = float(hist["amount"].mean())
    if avg == 0:
        return None
    return round(latest / avg, 2)


def summarize_institution_for_code(pro, code: str, start_date: str, end_date: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"errors": {}}
    symbol = code.split(".")[0]

    try:
        holders = call_api(pro.top10_floatholders, ts_code=code, start_date=start_date, end_date=end_date)
        if not holders.empty:
            date_col = "end_date" if "end_date" in holders.columns else None
            if date_col:
                latest_period = str(holders[date_col].max())
                latest = holders[holders[date_col].astype(str) == latest_period].copy()
            else:
                latest_period = None
                latest = holders.copy()
            summary["top10_floatholders"] = {
                "latest_period": latest_period,
                "rows": int(len(latest)),
                "holders": df_records(latest.head(10)),
            }
    except Exception as exc:
        summary["errors"]["top10_floatholders"] = str(exc)

    # Public fund portfolio is usually queried by fund code/report period, not efficiently by stock.
    # Keep fund holding aggregation out of the default live path to avoid expensive all-fund scans.
    summary["fund_portfolio"] = {"status": "not_fetched", "reason": "requires fund-code/report-period scan or local fund holdings cache"}

    try:
        reports = call_api(pro.report_rc, ts_code=code, start_date=start_date, end_date=end_date)
        if not reports.empty:
            rating_col = next((c for c in ["rating", "rating_name"] if c in reports.columns), None)
            org_col = next((c for c in ["org_name", "research_inst_name"] if c in reports.columns), None)
            date_col = next((c for c in ["report_date", "ann_date"] if c in reports.columns), None)
            reports_sorted = reports.sort_values(date_col, ascending=False) if date_col else reports
            summary["report_rc"] = {
                "rows": int(len(reports)),
                "rating_counts": dict(Counter(reports[rating_col].dropna().astype(str))) if rating_col else {},
                "org_count": int(reports[org_col].nunique()) if org_col else None,
                "latest_reports": df_records(reports_sorted.head(5)),
            }
    except Exception as exc:
        summary["errors"]["report_rc"] = str(exc)

    for api_name, kwargs in [
        ("stk_surv", {"ts_code": code, "start_date": start_date, "end_date": end_date}),
        ("top_inst", {"trade_date": end_date, "ts_code": code}),
    ]:
        try:
            func = getattr(pro, api_name)
            df = call_api(func, **kwargs)
            if not df.empty:
                summary[api_name] = {"rows": int(len(df)), "sample": df_records(df.head(10))}
        except Exception as exc:
            summary["errors"][api_name] = str(exc)

    if not summary["errors"]:
        summary.pop("errors", None)
    return summary


def fetch_institution_context(pro, codes: list[str], start_date: str, end_date: str) -> dict[str, Any]:
    return {code: summarize_institution_for_code(pro, code, start_date, end_date) for code in codes}


def aggregate_industry_institution(
    merged: pd.DataFrame, institution_context: dict[str, Any], industry_names: list[str], sample_per_industry: int
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if merged.empty or "industry" not in merged.columns:
        return result
    for industry in industry_names:
        sample = (
            merged[merged["industry"] == industry]
            .sort_values("amount", ascending=False)
            .head(sample_per_industry)["ts_code"]
            .astype(str)
            .tolist()
        )
        rating_counts: Counter[str] = Counter()
        fund_count = 0
        covered_codes = 0
        for code in sample:
            ctx = institution_context.get(code) or {}
            if ctx:
                covered_codes += 1
            report = ctx.get("report_rc") or {}
            rating_counts.update(report.get("rating_counts") or {})
            fund = ctx.get("fund_portfolio") or {}
            fund_count += int(fund.get("fund_count") or 0)
        result[industry] = {
            "sample_codes": sample,
            "sample_size": len(sample),
            "covered_codes": covered_codes,
            "rating_counts": dict(rating_counts),
            "fund_count_sum": fund_count,
        }
    return result


def fetch_history_for_codes(pro, codes: list[str], start_date: str, end_date: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        try:
            hist = call_api(pro.daily, ts_code=code, start_date=start_date, end_date=end_date)
        except Exception as exc:
            result[code] = {"error": str(exc)}
            continue
        if hist.empty:
            result[code] = {"rows": 0}
            continue
        hist = hist.sort_values("trade_date")
        latest = hist.iloc[-1]
        result[code] = {
            "rows": int(len(hist)),
            "pct_5d": pct_change(hist, min(5, len(hist))),
            "pct_10d": pct_change(hist, min(10, len(hist))),
            "pct_20d": pct_change(hist, min(20, len(hist))),
            "pct_30d": pct_change(hist, min(30, len(hist))),
            "latest_amount_yi": amount_yi(latest.get("amount")),
            "amount_vs_30d_avg": latest_amount_ratio(hist),
            "position_label": classify_position(hist),
            "latest_close": float(latest["close"]),
            "high_30d": float(hist["high"].max()),
            "low_30d": float(hist["low"].min()),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="latest", help="YYYYMMDD, YYYY-MM-DD, or latest")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--lookback", type=int, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--institution-lookback-days", type=int, default=365)
    parser.add_argument("--institution-top-n", type=int, default=5, help="Top turnover stocks to fetch institution context for.")
    args = parser.parse_args()

    pro = init_pro()
    trade_date, requested_closed = resolve_trade_date(pro, args.date)
    dates = recent_trade_dates(pro, trade_date, args.lookback)
    start_date = dates[0]

    errors: dict[str, str] = {}
    daily = call_api(pro.daily, trade_date=trade_date)
    if daily.empty:
        # Requested date may be open but not settled yet; fall back to latest previous date with rows.
        all_dates = recent_trade_dates(pro, trade_date, max(args.lookback, 30))
        for candidate in reversed(all_dates[:-1]):
            candidate_daily = call_api(pro.daily, trade_date=candidate)
            if not candidate_daily.empty:
                requested_closed = trade_date
                trade_date = candidate
                dates = recent_trade_dates(pro, trade_date, args.lookback)
                start_date = dates[0]
                daily = candidate_daily
                break
    basic = call_api(pro.stock_basic, exchange="", list_status="L", fields="ts_code,name,industry,market,exchange,list_date")
    daily_basic = pd.DataFrame()
    try:
        daily_basic = call_api(pro.daily_basic, trade_date=trade_date)
    except Exception as exc:
        errors["daily_basic"] = str(exc)

    merged = daily.merge(basic, on="ts_code", how="left") if not daily.empty else pd.DataFrame()
    if not daily_basic.empty and not merged.empty:
        keep_cols = [c for c in ["ts_code", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv", "circ_mv"] if c in daily_basic.columns]
        merged = merged.merge(daily_basic[keep_cols], on="ts_code", how="left")

    total_amount_yi = amount_yi(merged["amount"].sum()) if not merged.empty else None
    breadth = {}
    if not merged.empty:
        pct = merged["pct_chg"].astype(float)
        breadth = {
            "up": int((pct > 0).sum()),
            "down": int((pct < 0).sum()),
            "flat": int((pct == 0).sum()),
            "gt_5pct": int((pct >= 5).sum()),
            "lt_minus_5pct": int((pct <= -5).sum()),
            "limit_up_estimated": int((pct >= 9.8).sum()),
            "limit_down_estimated": int((pct <= -9.8).sum()),
        }

    turnover_top = merged.sort_values("amount", ascending=False).head(args.top_n).copy() if not merged.empty else pd.DataFrame()
    if not turnover_top.empty:
        turnover_top["amount_yi"] = turnover_top["amount"].map(amount_yi)

    industry_top = pd.DataFrame()
    industry_bottom = pd.DataFrame()
    if not merged.empty and "industry" in merged.columns:
        ind = merged.dropna(subset=["industry"]).groupby("industry").agg(
            stock_count=("ts_code", "count"),
            avg_pct_chg=("pct_chg", "mean"),
            amount_sum=("amount", "sum"),
            up_count=("pct_chg", lambda s: int((s.astype(float) > 0).sum())),
        ).reset_index()
        ind["amount_yi"] = ind["amount_sum"].map(amount_yi)
        industry_top = ind[ind["stock_count"] >= 3].sort_values(["avg_pct_chg", "amount_sum"], ascending=False).head(args.top_n)
        industry_bottom = ind[ind["stock_count"] >= 3].sort_values(["avg_pct_chg", "amount_sum"], ascending=[True, False]).head(args.top_n)

    indices = []
    for name, code in INDEX_CODES.items():
        try:
            idx = call_api(pro.index_daily, ts_code=code, start_date=start_date, end_date=trade_date)
            if idx.empty:
                continue
            latest = idx.sort_values("trade_date").iloc[-1].to_dict()
            latest["name"] = name
            latest["ts_code"] = code
            latest["pct_5d"] = pct_change(idx, min(5, len(idx)))
            latest["pct_10d"] = pct_change(idx, min(10, len(idx)))
            latest["pct_30d"] = pct_change(idx, min(30, len(idx)))
            latest["amount_vs_30d_avg"] = latest_amount_ratio(idx) if "amount" in idx.columns else None
            indices.append(latest)
        except Exception as exc:
            errors[f"index_daily:{code}"] = str(exc)

    moneyflow_top = []
    try:
        mf = call_api(pro.moneyflow, trade_date=trade_date)
        if not mf.empty:
            mf = mf.merge(basic[["ts_code", "name"]], on="ts_code", how="left")
            if "net_mf_amount" in mf.columns:
                moneyflow_top = df_records(mf.sort_values("net_mf_amount", ascending=False), args.top_n)
    except Exception as exc:
        errors["moneyflow"] = str(exc)

    target_codes = turnover_top["ts_code"].astype(str).tolist() if not turnover_top.empty else []
    history = fetch_history_for_codes(pro, target_codes, start_date, trade_date)

    institution_codes = target_codes[: max(0, args.institution_top_n)]
    institution_start_date = ymd(datetime.strptime(trade_date, "%Y%m%d").date() - timedelta(days=args.institution_lookback_days))
    institution_context = fetch_institution_context(pro, institution_codes, institution_start_date, trade_date) if institution_codes else {}
    industry_names = industry_top["industry"].astype(str).head(args.top_n).tolist() if not industry_top.empty else []
    industry_institution_context = aggregate_industry_institution(
        merged, institution_context, industry_names, max(1, min(args.institution_top_n, 5))
    )

    package = {
        "trade_date": trade_date,
        "requested_closed_date": requested_closed,
        "lookback_start_date": start_date,
        "total_amount_yi": total_amount_yi,
        "breadth": breadth,
        "indices": indices,
        "turnover_top": df_records(turnover_top, args.top_n),
        "turnover_top_30d_context": history,
        "turnover_top_institution_context": institution_context,
        "industry_top": df_records(industry_top, args.top_n),
        "industry_top_institution_context": industry_institution_context,
        "industry_bottom": df_records(industry_bottom, args.top_n),
        "moneyflow_top": moneyflow_top,
        "optional_errors": errors,
        "notes": [
            "daily.amount converted to 亿元 by amount/100000.",
            "Industry ranking is computed from stock_basic.industry when concept interfaces are unavailable.",
            "Industry institution context is aggregated from sampled top-turnover constituents.",
            "Limit up/down counts are estimated from pct_chg if limit_list_d is not used.",
        ],
    }

    text = json.dumps(package, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
