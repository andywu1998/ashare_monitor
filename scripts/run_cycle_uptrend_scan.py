#!/usr/bin/env python3
"""Scan all stocks and find names currently in an up-cycle."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.cycle import UptrendStock, find_uptrend_stocks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find A-share stocks that are currently in up-cycle."
    )
    parser.add_argument("--threshold", type=float, default=0.08, help="ZigZag reversal threshold")
    parser.add_argument("--min-gap", type=int, default=5, help="Minimum bars between pivots")
    parser.add_argument("--lookback-days", type=int, default=365, help="Lookback calendar days")
    parser.add_argument("--min-rows", type=int, default=60, help="Minimum rows per stock")
    parser.add_argument("--end-date", default="", help="End date YYYY-MM-DD; default today")
    parser.add_argument("--max-stocks", type=int, default=0, help="Debug limit; 0 means all")
    parser.add_argument("--top-k", type=int, default=200, help="Print/export top K records")
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional output path. Supports .md / .csv / .json",
    )
    return parser.parse_args()


def render_markdown(rows: List[UptrendStock]) -> str:
    lines = [
        "| 排名 | 股票 | 代码 | 最新交易日 | 最新收盘 | 周期涨跌幅 | 极值点数 | 周期数 | 距离上一个极值(交易日) |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for idx, r in enumerate(rows, start=1):
        chg = f"{r.latest_cycle_chg_pct:+.2f}%" if r.latest_cycle_chg_pct is not None else "n/a"
        lines.append(
            f"| {idx} | {r.name} | {r.ts_code} | {r.last_trade_date} | {r.last_close:.2f} | {chg} | {r.pivot_count} | {r.cycle_count} | {r.since_last_pivot_days} |"
        )
    return "\n".join(lines)


def write_output(path: Path, rows: List[UptrendStock]) -> None:
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".csv":
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ts_code",
                    "name",
                    "last_trade_date",
                    "last_close",
                    "pivot_count",
                    "cycle_count",
                    "since_last_pivot_days",
                    "latest_cycle_chg_pct",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
        return

    if suffix == ".json":
        payload = [asdict(row) for row in rows]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    # default markdown
    path.write_text(render_markdown(rows), encoding="utf-8")


def main() -> None:
    args = parse_args()
    end_date = date.fromisoformat(args.end_date) if args.end_date else None

    all_rows = find_uptrend_stocks(
        threshold=args.threshold,
        min_gap=args.min_gap,
        lookback_days=args.lookback_days,
        min_rows=args.min_rows,
        end_date=end_date,
        max_stocks=args.max_stocks,
    )
    top_k = max(1, args.top_k)
    rows = all_rows[:top_k]

    print(
        f"uptrend_stocks={len(all_rows)} shown={len(rows)} threshold={args.threshold} min_gap={args.min_gap} lookback_days={args.lookback_days}"
    )
    print(render_markdown(rows))

    if args.output_file:
        output_path = Path(args.output_file).expanduser().resolve()
    else:
        today = (end_date or date.today()).isoformat().replace("-", "")
        output_path = (
            Path(__file__).resolve().parents[1]
            / "reports"
            / f"uptrend_scan_{today}.md"
        )

    write_output(output_path, rows)
    print(f"[saved] {output_path}")


if __name__ == "__main__":
    main()
