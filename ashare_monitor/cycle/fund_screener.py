"""Cycle screener for all funds based on local MySQL data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from ashare_monitor.stock_sync.db import (
    fetch_all_funds_with_last_trade,
    fetch_daily_rows_for_fund,
)

from .core import current_trend, zigzag_pivots


@dataclass
class UptrendFund:
    ts_code: str
    name: str
    last_trade_date: str
    last_close: float
    pivot_count: int
    cycle_count: int
    since_last_pivot_days: int
    latest_cycle_chg_pct: Optional[float]


def _to_dict_rows(raw_rows: List[tuple]) -> List[Dict[str, Optional[float]]]:
    out: List[Dict[str, Optional[float]]] = []
    for row in raw_rows:
        trade_date, open_p, high_p, low_p, close_p, pre_close_p, change_p, pct_chg_p, vol_p, amount_p = row
        if close_p is None:
            continue
        close_v = float(close_p)
        open_v = float(open_p) if open_p is not None else close_v
        high_v = float(high_p) if high_p is not None else max(open_v, close_v)
        low_v = float(low_p) if low_p is not None else min(open_v, close_v)
        pre_close_v = float(pre_close_p) if pre_close_p is not None else close_v
        out.append(
            {
                "trade_date": str(trade_date),
                "open": open_v,
                "high": high_v,
                "low": low_v,
                "close": close_v,
                "pre_close": pre_close_v,
                "change": float(change_p) if change_p is not None else None,
                "pct_chg": float(pct_chg_p) if pct_chg_p is not None else None,
                "vol": float(vol_p) if vol_p is not None else None,
                "amount": float(amount_p) if amount_p is not None else None,
            }
        )
    return out


def find_uptrend_funds(
    threshold: float = 0.08,
    min_gap: int = 5,
    lookback_days: int = 365,
    min_rows: int = 60,
    end_date: Optional[date] = None,
    max_funds: int = 0,
    mysql_config: Optional[dict] = None,
) -> List[UptrendFund]:
    real_end = end_date or date.today()
    start_date = real_end - timedelta(days=lookback_days)
    fund_rows = fetch_all_funds_with_last_trade(mysql_config=mysql_config)
    if max_funds > 0:
        fund_rows = fund_rows[:max_funds]

    results: List[UptrendFund] = []
    for ts_code, name, _last_trade in fund_rows:
        raw = fetch_daily_rows_for_fund(
            ts_code,
            start_date,
            real_end,
            mysql_config=mysql_config,
        )
        rows = _to_dict_rows(raw)
        if len(rows) < min_rows:
            continue
        pivots = zigzag_pivots(rows, threshold=threshold, min_gap=min_gap)
        trend = current_trend(pivots)
        if trend != "up":
            continue

        latest_idx = len(rows) - 1
        last_pivot_idx = pivots[-1][0] if pivots else latest_idx
        cycle_chg = None
        if len(pivots) >= 2 and pivots[-2][2]:
            cycle_chg = (pivots[-1][2] / pivots[-2][2] - 1) * 100
        results.append(
            UptrendFund(
                ts_code=ts_code,
                name=name or ts_code,
                last_trade_date=str(rows[-1]["trade_date"]),
                last_close=float(rows[-1]["close"] or 0),
                pivot_count=len(pivots),
                cycle_count=max(0, len(pivots) - 1),
                since_last_pivot_days=max(0, latest_idx - last_pivot_idx),
                latest_cycle_chg_pct=round(cycle_chg, 2) if cycle_chg is not None else None,
            )
        )

    results.sort(
        key=lambda x: (
            x.latest_cycle_chg_pct if x.latest_cycle_chg_pct is not None else -1e9,
            x.since_last_pivot_days,
            x.last_close,
        ),
        reverse=True,
    )
    return results
