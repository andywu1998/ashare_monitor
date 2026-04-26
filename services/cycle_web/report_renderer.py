"""Cycle report payload builder and Jinja2 HTML renderer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATE_VERSION_V1 = "cycle_v1"
DEFAULT_TEMPLATE_VERSION = TEMPLATE_VERSION_V1

TEMPLATE_NAME_MAP = {
    TEMPLATE_VERSION_V1: "cycle_report_v1.html.j2",
}

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _num(v: Optional[float], digits: int = 4) -> float:
    if v is None:
        return 0.0
    return round(float(v), digits)


def build_cycle_payload(
    *,
    stock_name: str,
    ts_code: str,
    rows: list[dict],
    pivots: list[tuple[int, str, float]],
    threshold: float,
    min_gap: int,
    asset_type: str,
    asset_label: str,
) -> dict:
    candles: list[dict] = []
    for row in rows:
        candles.append(
            {
                "trade_date": str(row.get("trade_date") or ""),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "close": _num(row.get("close")),
                "pct_chg": _num(row.get("pct_chg")),
                "vol": _num(row.get("vol")),
                "amount": _num(row.get("amount")),
            }
        )

    pivot_items: list[dict] = []
    for idx, ptype, price in pivots:
        trade_date = ""
        if 0 <= idx < len(candles):
            trade_date = candles[idx]["trade_date"]
        pivot_items.append(
            {
                "idx": int(idx),
                "type": str(ptype),
                "price": _num(price),
                "trade_date": trade_date,
            }
        )

    cycles: list[dict] = []
    for i in range(len(pivots) - 1):
        i1, t1, p1 = pivots[i]
        i2, t2, p2 = pivots[i + 1]
        chg_pct = None
        if p1:
            chg_pct = round((float(p2) / float(p1) - 1) * 100, 2)
        start_date = candles[i1]["trade_date"] if 0 <= i1 < len(candles) else ""
        end_date = candles[i2]["trade_date"] if 0 <= i2 < len(candles) else ""
        cycles.append(
            {
                "index": i + 1,
                "start_idx": int(i1),
                "end_idx": int(i2),
                "start_type": str(t1),
                "end_type": str(t2),
                "start_date": start_date,
                "end_date": end_date,
                "start_price": _num(p1),
                "end_price": _num(p2),
                "trade_days": max(0, int(i2) - int(i1)),
                "chg_pct": chg_pct,
            }
        )

    closes = [x["close"] for x in candles] or [0.0]
    latest = candles[-1] if candles else {}

    summary = {
        "stock_name": stock_name,
        "ts_code": ts_code,
        "asset_type": asset_type,
        "asset_label": asset_label,
        "date_range": [
            candles[0]["trade_date"] if candles else "",
            candles[-1]["trade_date"] if candles else "",
        ],
        "sample_days": len(candles),
        "min_close": round(min(closes), 4),
        "max_close": round(max(closes), 4),
        "latest_date": latest.get("trade_date", ""),
        "latest_close": latest.get("close", 0.0),
        "latest_pct_chg": latest.get("pct_chg", 0.0),
        "latest_vol": latest.get("vol", 0.0),
        "latest_amount": latest.get("amount", 0.0),
        "pivot_count": len(pivot_items),
        "cycle_count": len(cycles),
        "threshold": threshold,
        "min_gap": min_gap,
    }

    return {
        "stock_name": stock_name,
        "ts_code": ts_code,
        "asset_type": asset_type,
        "asset_label": asset_label,
        "threshold": threshold,
        "min_gap": min_gap,
        "summary": summary,
        "candles": candles,
        "pivots": pivot_items,
        "cycles": cycles,
    }


def render_cycle_report_html(
    *,
    report_data: dict,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
) -> str:
    safe_ver = (template_version or DEFAULT_TEMPLATE_VERSION).strip()
    template_name = TEMPLATE_NAME_MAP.get(safe_ver)
    if not template_name:
        raise ValueError(f"unsupported template_version: {safe_ver}")
    template = _ENV.get_template(template_name)
    summary = report_data.get("summary") or {}
    title = (
        f"{summary.get('stock_name', '-')}"
        f"（{summary.get('ts_code', '-')}）"
        f"{summary.get('asset_label', '')}周期分析"
    )
    return template.render(
        title=title,
        summary=summary,
        cycles=(report_data.get("cycles") or []),
        report_data_json=json.dumps(report_data, ensure_ascii=False),
        template_version=safe_ver,
    )

