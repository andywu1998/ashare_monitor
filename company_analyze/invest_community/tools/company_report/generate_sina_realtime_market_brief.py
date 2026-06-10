#!/usr/bin/env python3
"""Generate an A-share realtime market brief Markdown report from Sina data.

This is a standalone experiment and intentionally does not modify or call the
existing Tushare-based daily brief flow.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


ASHARE_MONITOR_ROOT = Path(__file__).resolve().parents[4]
if str(ASHARE_MONITOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ASHARE_MONITOR_ROOT))

from ashare_monitor.config import load_config  # noqa: E402
from ashare_monitor.providers.sina import SinaProvider  # noqa: E402


DEFAULT_CONFIG = ASHARE_MONITOR_ROOT / "configs" / "config.toml"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fmt_pct(value: Any) -> str:
    return f"{_float(value):+.2f}%"


def _fmt_yi(value: Any) -> str:
    return f"{_float(value):.2f}亿元"


def _normalize_symbol(raw: str) -> str:
    value = (raw or "").lower()
    if len(value) <= 2:
        return value.upper()
    prefix, code = value[:2], value[2:]
    suffix = prefix.upper()
    if suffix in {"SH", "SZ", "BJ"}:
        return f"{code.upper()}.{suffix}"
    return value.upper()


def _stock_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or row.get("code") or row.get("symbol") or "")


def _amount_yi(row: dict[str, Any]) -> float:
    return round(_float(row.get("amount")) / 1e8, 2)


def _safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("symbol") or not row.get("name"):
            continue
        if _float(row.get("trade")) <= 0 and _float(row.get("amount")) <= 0:
            continue
        result.append(row)
    return result


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "无可用数据。\n"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines) + "\n"


def _build_snapshot(provider: SinaProvider, top_n: int) -> dict[str, Any]:
    stock_rows = _safe_rows(provider._fetch_stock_rows())
    dataset = provider.fetch()

    ranked_by_amount = sorted(stock_rows, key=_amount_yi, reverse=True)
    ranked_by_gain = sorted(stock_rows, key=lambda row: _float(row.get("changepercent")), reverse=True)
    ranked_by_loss = sorted(stock_rows, key=lambda row: _float(row.get("changepercent")))
    tick_times = sorted({str(row.get("ticktime")) for row in stock_rows if row.get("ticktime")})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "Sina realtime quote APIs",
        "stock_count": len(stock_rows),
        "ticktime_min": tick_times[0] if tick_times else "",
        "ticktime_max": tick_times[-1] if tick_times else "",
        "total_amount_yi": round(sum(_amount_yi(row) for row in stock_rows), 2),
        "indices": [asdict(item) for item in dataset.indices],
        "breadth": asdict(dataset.breadth),
        "top_sectors": [asdict(item) for item in dataset.top_sectors],
        "bottom_sectors": [asdict(item) for item in dataset.bottom_sectors],
        "capital_flow": asdict(dataset.capital_flow),
        "turnover_top": ranked_by_amount[:top_n],
        "gain_top": ranked_by_gain[:top_n],
        "loss_top": ranked_by_loss[:top_n],
    }


def _stock_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        out.append(
            [
                _stock_name(row),
                _normalize_symbol(str(row.get("symbol") or "")),
                _fmt_pct(row.get("changepercent")),
                _amount_yi(row),
                _float(row.get("trade")),
                _float(row.get("turnoverratio")),
                row.get("ticktime") or "",
            ]
        )
    return out


def build_markdown(snapshot: dict[str, Any]) -> str:
    breadth = snapshot["breadth"]
    up = int(breadth.get("advancing") or 0)
    down = int(breadth.get("declining") or 0)
    flat = int(breadth.get("unchanged") or 0)
    limit_up = int(breadth.get("limit_up") or 0)
    limit_down = int(breadth.get("limit_down") or 0)
    total = up + down + flat
    mood = "偏强" if up > down * 1.25 else "偏弱" if down > up * 1.25 else "分化"

    lines: list[str] = []
    lines.append(f"# A股实时走势分析（Sina，{datetime.now().strftime('%Y%m%d')}）")
    lines.append("")
    lines.append(f"- 生成时间：{snapshot['generated_at']}")
    lines.append(f"- 取数来源：{snapshot['source']}")
    lines.append(f"- 样本数量：{snapshot['stock_count']} 只")
    if snapshot.get("ticktime_max"):
        lines.append(f"- 行情时间：{snapshot.get('ticktime_min') or '未知'} 至 {snapshot['ticktime_max']}")
    lines.append("- 数据口径：Sina 实时/延时快照，不依赖 Tushare daily 日线落库。")
    lines.append("")

    lines.append("## 1. 今日总体判断")
    lines.append(
        f"截至快照时间，A股整体表现{mood}：上涨 {up} 家、下跌 {down} 家、平盘 {flat} 家，"
        f"涨停约 {limit_up} 家、跌停约 {limit_down} 家；样本成交额合计约 {_fmt_yi(snapshot['total_amount_yi'])}。"
    )
    if total:
        lines.append(f"上涨占比约 {up / total * 100:.1f}%，下跌占比约 {down / total * 100:.1f}%。")
    lines.append("")

    lines.append("## 2. 指数与量能")
    index_rows = []
    for item in snapshot["indices"]:
        index_rows.append(
            [
                item.get("name"),
                item.get("symbol"),
                f"{_float(item.get('last')):.2f}",
                f"{_float(item.get('change')):+.2f}",
                _fmt_pct(item.get("change_percent")),
                _fmt_yi(item.get("turnover")) if item.get("turnover") is not None else "未知",
            ]
        )
    lines.append(_table(["指数", "代码", "点位", "涨跌", "涨跌幅", "成交额"], index_rows))
    lines.append("")

    lines.append("## 3. 市场广度")
    lines.append(
        _table(
            ["指标", "数值"],
            [
                ["上涨", up],
                ["下跌", down],
                ["平盘", flat],
                ["涨停估算", limit_up],
                ["跌停估算", limit_down],
                ["样本成交额", _fmt_yi(snapshot["total_amount_yi"])],
            ],
        )
    )
    lines.append("")

    lines.append("## 4. 行业强弱")
    lines.append(
        _table(
            ["领涨行业", "涨跌幅", "龙头"],
            [
                [item.get("name"), _fmt_pct(item.get("change_percent")), "/".join(item.get("leaders") or [])]
                for item in snapshot["top_sectors"]
            ],
        )
    )
    lines.append(
        _table(
            ["领跌行业", "涨跌幅", "代表"],
            [
                [item.get("name"), _fmt_pct(item.get("change_percent")), "/".join(item.get("leaders") or [])]
                for item in snapshot["bottom_sectors"]
            ],
        )
    )
    lines.append("")

    lines.append("## 5. 成交额 Top 个股")
    lines.append(_table(["名称", "代码", "涨跌幅", "成交额(亿元)", "现价", "换手率", "时间"], _stock_rows(snapshot["turnover_top"])))
    lines.append("")

    lines.append("## 6. 涨跌幅 Top")
    lines.append("### 领涨")
    lines.append(_table(["名称", "代码", "涨跌幅", "成交额(亿元)", "现价", "换手率", "时间"], _stock_rows(snapshot["gain_top"])))
    lines.append("")
    lines.append("### 领跌")
    lines.append(_table(["名称", "代码", "涨跌幅", "成交额(亿元)", "现价", "换手率", "时间"], _stock_rows(snapshot["loss_top"])))
    lines.append("")

    flow = snapshot["capital_flow"]
    lines.append("## 7. 资金与口径说明")
    north = flow.get("northbound")
    south = flow.get("southbound")
    lines.append(f"- 北向资金：{_fmt_yi(north) if north is not None else '未取到/接口不可用'}")
    lines.append(f"- 南向资金：{_fmt_yi(south) if south is not None else '未取到/接口不可用'}")
    lines.append("- 全市场成交额来自 Sina 个股快照 `amount` 汇总，和交易所正式口径可能存在延迟或样本差异。")
    lines.append("- 涨跌停数量按涨跌幅阈值估算；北交所、新股、ST 等涨跌幅规则未逐一精确拆分。")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Sina realtime A-share market brief.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to ashare_monitor config.toml")
    parser.add_argument("--top-n", type=int, default=20, help="Rows per Top table")
    parser.add_argument("--page-size", type=int, default=None, help="Override Sina page size, max 100")
    parser.add_argument("--max-pages", type=int, default=None, help="Override Sina max pages")
    parser.add_argument("--output-dir", default=None, help="Directory for Markdown report")
    parser.add_argument("--output-file", default=None, help="Exact Markdown output path")
    parser.add_argument("--dump-json", default=None, help="Optional JSON snapshot path")
    parser.add_argument("--dry-run", action="store_true", help="Print report without writing files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    nested_extras = cfg.provider.extras.pop("extras", None)
    if isinstance(nested_extras, dict):
        cfg.provider.extras.update(nested_extras)
    if args.page_size is not None:
        cfg.provider.extras["page_size"] = args.page_size
    if args.max_pages is not None:
        cfg.provider.extras["max_pages"] = args.max_pages

    provider = SinaProvider(cfg)
    snapshot = _build_snapshot(provider, max(1, args.top_n))
    markdown = build_markdown(snapshot)
    print(markdown)

    if args.dump_json:
        json_path = Path(args.dump_json).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成JSON：{json_path}")

    if not args.dry_run:
        if args.output_file:
            output_path = Path(args.output_file).expanduser().resolve()
        else:
            output_dir = (
                Path(args.output_dir).expanduser().resolve()
                if args.output_dir
                else Path(__file__).resolve().parent / "reports"
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"A股实时走势分析_Sina_{timestamp}_report.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"已生成报告：{output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
