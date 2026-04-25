#!/usr/bin/env python3
"""Generate a markdown table for top A-share stocks by daily volume."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.config import AppConfig, load_config
from ashare_monitor.providers.sina import SinaProvider


@dataclass(slots=True)
class StockRow:
    symbol: str
    name: str
    price: float
    change_percent: float
    volume: float
    amount: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Top A-share stocks by volume")
    parser.add_argument(
        "--config",
        default=str(ROOT_DIR / "configs" / "config.toml"),
        help="Path to TOML config",
    )
    parser.add_argument("--top-k", type=int, default=10, help="Number of rows to display")
    parser.add_argument(
        "--sort-by",
        choices=("volume", "amount"),
        default="amount",
        help="Sort by volume (shares) or amount (CNY)",
    )
    parser.add_argument("--output-file", help="Optional output file path")
    return parser.parse_args()


def _to_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_symbol(raw_symbol: str) -> str:
    raw = (raw_symbol or "").lower()
    if len(raw) <= 2:
        return raw_symbol.upper()
    prefix, code = raw[:2], raw[2:]
    suffix = prefix.upper()
    if suffix in {"SH", "SZ", "BJ"}:
        return f"{code.upper()}.{suffix}"
    return raw_symbol.upper()


def _fetch_rows(cfg: AppConfig) -> List[StockRow]:
    provider = SinaProvider(cfg)
    rows = provider._fetch_stock_rows()
    results: List[StockRow] = []
    for row in rows:
        results.append(
            StockRow(
                symbol=_normalize_symbol(str(row.get("symbol", ""))),
                name=str(row.get("name", "")),
                price=_to_float(row.get("trade")),
                change_percent=_to_float(row.get("changepercent")),
                volume=_to_float(row.get("volume")),
                amount=_to_float(row.get("amount")),
            )
        )
    return results


def _render_table(rows: List[StockRow], top_k: int, sort_by: str) -> str:
    key = (lambda item: item.volume) if sort_by == "volume" else (lambda item: item.amount)
    ranked = sorted(rows, key=key, reverse=True)[: max(1, top_k)]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    metric = "成交量" if sort_by == "volume" else "成交额"
    lines = [f"A股{metric}前{len(ranked)}（{ts}）", ""]
    lines.append("| 排名 | 代码 | 名称 | 成交量(万股) | 成交额(亿元) | 最新价 | 涨跌幅 |")
    lines.append("|---:|---|---|---:|---:|---:|---:|")
    for index, item in enumerate(ranked, start=1):
        vol_wan = item.volume / 10000
        amt_yi = item.amount / 1e8
        lines.append(
            f"| {index} | {item.symbol} | {item.name} | {vol_wan:.2f} | {amt_yi:.2f} | {item.price:.2f} | {item.change_percent:+.2f}% |"
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    rows = _fetch_rows(cfg)
    output = _render_table(rows, args.top_k, args.sort_by)
    print(output)

    if args.output_file:
        output_path = Path(args.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"\n[saved] {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
