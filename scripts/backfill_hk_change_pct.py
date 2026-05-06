#!/usr/bin/env python3
"""Backfill HK daily pre_close/change/pct_chg using previous trading close."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
import sys

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.stock_sync.config import load_env_from_zshrc
from ashare_monitor.stock_sync.db import get_connection


def load_mysql_env_fallback_from_zshrc() -> None:
    """Fallback loader when zsh is unavailable in runtime env."""
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    pattern = re.compile(r"^\s*export\s+(MYSQL_(?:HOST|PORT|USER|PASSWORD|DATABASE))=(.+?)\s*$")
    for raw_line in zshrc.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if (value.startswith("'") and value.endswith("'")) or (
            value.startswith('"') and value.endswith('"')
        ):
            value = value[1:-1]
        if os.getenv(key) in (None, ""):
            os.environ[key] = value


@dataclass
class CalcRow:
    trade_date: object
    close: Optional[float]
    old_pre_close: Optional[float]
    old_change: Optional[float]
    old_pct_chg: Optional[float]
    new_pre_close: Optional[float]
    new_change: Optional[float]
    new_pct_chg: Optional[float]


def as_float(v) -> Optional[float]:
    if v is None:
        return None
    return float(v)


def resolve_ts_code(ts_code: str | None, name_like: str | None) -> str:
    if ts_code:
        return ts_code.strip().upper()
    if not name_like:
        raise ValueError("missing --ts-code and --name-like")
    sql = """
    SELECT ts_code
    FROM stock_basic
    WHERE asset_type='H'
      AND name LIKE %s
    ORDER BY ts_code
    LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (f"%{name_like}%",))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"cannot find HK symbol by name_like={name_like!r}")
    return str(row[0]).strip().upper()


def fetch_hk_ts_codes() -> list[str]:
    sql = """
    SELECT DISTINCT ts_code
    FROM stock_daily
    WHERE asset_type='H'
    ORDER BY ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [str(r[0]).strip().upper() for r in cur.fetchall()]


def build_calc_rows(ts_code: str, days: int) -> list[CalcRow]:
    query_all = int(days) <= 0
    if query_all:
        sql = """
        SELECT trade_date, close, pre_close, `change`, pct_chg
        FROM stock_daily
        WHERE ts_code=%s
          AND asset_type='H'
        ORDER BY trade_date DESC
        """
        params = (ts_code,)
    else:
        limit = max(1, int(days)) + 1
        sql = """
        SELECT trade_date, close, pre_close, `change`, pct_chg
        FROM stock_daily
        WHERE ts_code=%s
          AND asset_type='H'
        ORDER BY trade_date DESC
        LIMIT %s
        """
        params = (ts_code, limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            raw_rows = list(cur.fetchall())
    if not raw_rows:
        return []

    if query_all:
        seed_prev_close = None
        target_rows_desc = raw_rows
    else:
        seed_prev_close = as_float(raw_rows[-1][1]) if len(raw_rows) > days else None
        target_rows_desc = raw_rows[:days]
    target_rows_asc = list(reversed(target_rows_desc))

    prev_close = seed_prev_close
    calc_rows: list[CalcRow] = []
    for trade_date, close, old_pre, old_change, old_pct in target_rows_asc:
        close_v = as_float(close)
        old_pre_v = as_float(old_pre)
        old_change_v = as_float(old_change)
        old_pct_v = as_float(old_pct)

        new_pre = prev_close if prev_close is not None else None
        new_change = None
        new_pct = None
        if close_v is not None and new_pre is not None:
            new_change = round(close_v - new_pre, 4)
            if new_pre != 0:
                new_pct = round(new_change / new_pre * 100, 4)

        calc_rows.append(
            CalcRow(
                trade_date=trade_date,
                close=close_v,
                old_pre_close=old_pre_v,
                old_change=old_change_v,
                old_pct_chg=old_pct_v,
                new_pre_close=new_pre,
                new_change=new_change,
                new_pct_chg=new_pct,
            )
        )

        if close_v is not None:
            prev_close = close_v

    return calc_rows


def apply_rows(ts_code: str, calc_rows: list[CalcRow]) -> int:
    sql = """
    UPDATE stock_daily
    SET pre_close=%s, `change`=%s, pct_chg=%s
    WHERE ts_code=%s
      AND asset_type='H'
      AND trade_date=%s
    """
    params = [
        (row.new_pre_close, row.new_change, row.new_pct_chg, ts_code, row.trade_date)
        for row in calc_rows
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            affected = cur.executemany(sql, params)
        conn.commit()
    return int(affected or 0)


def bulk_recalc_all_hk_via_sql() -> int:
    sql = """
    UPDATE stock_daily d
    JOIN (
        SELECT
            ts_code,
            trade_date,
            LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS new_pre_close,
            CASE
                WHEN close IS NOT NULL
                 AND LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) IS NOT NULL
                THEN ROUND(close - LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date), 4)
                ELSE NULL
            END AS new_change,
            CASE
                WHEN close IS NOT NULL
                 AND LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) IS NOT NULL
                 AND LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) <> 0
                THEN ROUND(
                    (close - LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date))
                    / LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) * 100,
                    4
                )
                ELSE NULL
            END AS new_pct_chg
        FROM stock_daily
        WHERE asset_type='H'
    ) x
      ON d.ts_code = x.ts_code
     AND d.trade_date = x.trade_date
     AND d.asset_type = 'H'
    SET
        d.pre_close = x.new_pre_close,
        d.`change` = x.new_change,
        d.pct_chg = x.new_pct_chg
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            affected = cur.execute(sql)
        conn.commit()
    return int(affected or 0)


def fmt(v: Optional[float]) -> str:
    return "None" if v is None else f"{v:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill HK pre_close/change/pct_chg from previous close."
    )
    parser.add_argument("--ts-code", type=str, default="", help="HK ts_code, e.g. 09660.HK")
    parser.add_argument("--name-like", type=str, default="地平线机器人", help="Fallback name matcher.")
    parser.add_argument("--days", type=int, default=30, help="Latest trading rows to backfill; <=0 means all history.")
    parser.add_argument("--all-hk", action="store_true", help="Run for all HK ts_code in stock_daily.")
    parser.add_argument("--quiet", action="store_true", help="Do not print per-day details.")
    parser.add_argument("--fast-sql", action="store_true", help="Use one-shot SQL recalculation for all HK full history.")
    parser.add_argument("--apply", action="store_true", help="Write calculated values back to DB.")
    args = parser.parse_args()

    load_env_from_zshrc(override=False)
    load_mysql_env_fallback_from_zshrc()

    if args.all_hk:
        if args.fast_sql and args.days <= 0 and args.apply:
            affected = bulk_recalc_all_hk_via_sql()
            print(f"bulk_sql_done affected_rows={affected}")
            return
        ts_codes = fetch_hk_ts_codes()
    else:
        ts_codes = [resolve_ts_code(args.ts_code, args.name_like)]

    total_codes = len(ts_codes)
    total_rows = 0
    total_updated = 0
    print(f"codes={total_codes} days={args.days} apply={args.apply} all_hk={args.all_hk}")
    for i, ts_code in enumerate(ts_codes, start=1):
        calc_rows = build_calc_rows(ts_code=ts_code, days=args.days)
        row_count = len(calc_rows)
        total_rows += row_count
        if not calc_rows:
            print(f"[{i}/{total_codes}] ts_code={ts_code} rows=0 skip")
            continue

        if not args.quiet:
            print(f"ts_code={ts_code} rows={row_count} apply={args.apply}")
            print(
                "trade_date close old_pre -> new_pre old_change -> new_change old_pct -> new_pct"
            )
            for row in calc_rows:
                print(
                    f"{row.trade_date} {fmt(row.close)} "
                    f"{fmt(row.old_pre_close)} -> {fmt(row.new_pre_close)} "
                    f"{fmt(row.old_change)} -> {fmt(row.new_change)} "
                    f"{fmt(row.old_pct_chg)} -> {fmt(row.new_pct_chg)}"
                )

        updated = 0
        if args.apply:
            updated = apply_rows(ts_code, calc_rows)
            total_updated += updated
        print(f"[{i}/{total_codes}] ts_code={ts_code} rows={row_count} updated={updated}")

    print(
        f"summary codes={total_codes} rows={total_rows} updated_rows={total_updated} apply={args.apply}"
    )


if __name__ == "__main__":
    main()
