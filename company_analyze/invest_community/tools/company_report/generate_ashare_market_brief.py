#!/usr/bin/env python3
"""Generate an A-share market brief Markdown report from real Tushare data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ASHARE_MONITOR_ROOT = ROOT.parent
PROJECT_SKILL_SCRIPT = ASHARE_MONITOR_ROOT / ".codex" / "skills" / "tushare-ashare-market-brief" / "scripts" / "fetch_ashare_market_brief.py"
GLOBAL_SKILL_SCRIPT = Path.home() / ".codex" / "skills" / "tushare-ashare-market-brief" / "scripts" / "fetch_ashare_market_brief.py"
SKILL_SCRIPT = PROJECT_SKILL_SCRIPT if PROJECT_SKILL_SCRIPT.exists() else GLOBAL_SKILL_SCRIPT


def _slugify(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text).strip("_") or "ashare_market"


def _run_fetch(date: str, top_n: int, lookback: int, institution_top_n: int, institution_lookback_days: int) -> dict[str, Any]:
    if not SKILL_SCRIPT.exists():
        raise RuntimeError(f"缺少取数脚本: {SKILL_SCRIPT}")
    output = Path(os.getenv("ASHARE_MARKET_BRIEF_JSON", "/tmp/ashare_market_brief_company_analyze.json"))
    cmd = [
        sys.executable,
        str(SKILL_SCRIPT),
        "--date",
        date,
        "--top-n",
        str(top_n),
        "--lookback",
        str(lookback),
        "--institution-top-n",
        str(institution_top_n),
        "--institution-lookback-days",
        str(institution_lookback_days),
        "--output",
        str(output),
    ]
    subprocess.run(cmd, check=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "未知"


def _fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "未知"
    try:
        return f"{float(value):.2f}{suffix}"
    except Exception:
        return f"{value}{suffix}"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "无可用数据。\n"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(x).replace("\n", " ") for x in row) + " |")
    return "\n".join(out) + "\n"


def _rating_summary(ctx: dict[str, Any]) -> str:
    report = ctx.get("report_rc") or {}
    counts = report.get("rating_counts") or {}
    if not counts:
        return "无评级数据/未取到"
    return "，".join(f"{k}:{v}" for k, v in counts.items())


def _holder_summary(ctx: dict[str, Any]) -> str:
    holders = ctx.get("top10_floatholders") or {}
    if not holders:
        return "无前十大流通股东数据/未取到"
    return f"{holders.get('latest_period') or '未知期间'}，{holders.get('rows', 0)}条"


def _institution_errors(ctx: dict[str, Any]) -> str:
    errors = ctx.get("errors") or {}
    if not errors:
        return ""
    return "; ".join(f"{k}: {v}" for k, v in errors.items())[:180]


def build_markdown(data: dict[str, Any]) -> str:
    trade_date = data.get("trade_date", "未知")
    requested_closed = data.get("requested_closed_date")
    title_date = trade_date
    lines: list[str] = []
    lines.append(f"# A股今日走势分析（{title_date}）")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 取数来源：Tushare SDK")
    lines.append(f"- 实际交易日：{trade_date}")
    if requested_closed:
        lines.append(f"- 请求日期 {requested_closed} 非可用交易日/数据未落库，已回退到 {trade_date}")
    lines.append(f"- 30日上下文起点：{data.get('lookback_start_date', '未知')}")
    lines.append("")

    breadth = data.get("breadth") or {}
    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    total_amount = data.get("total_amount_yi")
    limit_up = breadth.get("limit_up_estimated", 0)
    limit_down = breadth.get("limit_down_estimated", 0)
    lines.append("## 1. 今日总体判断")
    if up and down:
        mood = "偏弱" if down > up * 1.5 else "分化" if down > up else "偏强"
        lines.append(
            f"{trade_date} A股整体表现{mood}：上涨 {up} 家、下跌 {down} 家，两市成交额约 {_fmt_num(total_amount, '亿元')}。"
            f"估算涨停 {limit_up} 家、跌停 {limit_down} 家，说明短线情绪仍需要结合主线和亏钱效应判断。"
        )
    else:
        lines.append("市场广度数据不足，需结合指数与成交额进一步判断。")
    lines.append("")

    lines.append("## 2. 指数与量能")
    index_rows = []
    for item in data.get("indices") or []:
        index_rows.append([
            item.get("name"),
            item.get("ts_code"),
            _fmt_pct(item.get("pct_chg")),
            _fmt_pct(item.get("pct_5d")),
            _fmt_pct(item.get("pct_10d")),
            _fmt_pct(item.get("pct_30d")),
        ])
    lines.append(_table(["指数", "代码", "今日涨跌", "5日", "10日", "30日"], index_rows))
    lines.append("")

    lines.append("## 3. 市场广度")
    lines.append(_table(["指标", "数值"], [[k, v] for k, v in breadth.items()]))
    lines.append("")

    lines.append("## 4. 行业/概念/题材强弱")
    lines.append("说明：当前脚本默认用 `stock_basic.industry` 聚合行业；同花顺概念/题材接口如不可用，不强行编造。")
    industry_rows = []
    inst_by_ind = data.get("industry_top_institution_context") or {}
    for item in data.get("industry_top") or []:
        industry = item.get("industry")
        inst = inst_by_ind.get(industry, {})
        industry_rows.append([
            industry,
            item.get("stock_count"),
            _fmt_pct(item.get("avg_pct_chg")),
            _fmt_num(item.get("amount_yi"), "亿元"),
            inst.get("sample_size", 0),
            inst.get("rating_counts", {}),
        ])
    lines.append(_table(["行业", "股票数", "平均涨跌", "成交额", "机构样本数", "样本评级分布"], industry_rows))
    lines.append("")

    lines.append("## 5. 成交额 Top 个股与30日位置")
    hist = data.get("turnover_top_30d_context") or {}
    stock_rows = []
    for item in data.get("turnover_top") or []:
        code = item.get("ts_code")
        ctx = hist.get(code, {})
        stock_rows.append([
            item.get("name"),
            code,
            _fmt_pct(item.get("pct_chg")),
            _fmt_num(item.get("amount_yi"), "亿元"),
            _fmt_pct(ctx.get("pct_5d")),
            _fmt_pct(ctx.get("pct_10d")),
            _fmt_pct(ctx.get("pct_30d")),
            ctx.get("amount_vs_30d_avg", "未知"),
            ctx.get("position_label", "未知"),
        ])
    lines.append(_table(["名称", "代码", "今日涨跌", "成交额", "5日", "10日", "30日", "量能/30日均值", "30日位置"], stock_rows))
    lines.append("")

    lines.append("## 6. 机构持仓与机构评级")
    inst = data.get("turnover_top_institution_context") or {}
    inst_rows = []
    for item in data.get("turnover_top") or []:
        code = item.get("ts_code")
        ctx = inst.get(code, {})
        inst_rows.append([
            item.get("name"),
            code,
            _holder_summary(ctx),
            _rating_summary(ctx),
            (ctx.get("stk_surv") or {}).get("rows", 0),
            _institution_errors(ctx),
        ])
    lines.append(_table(["名称", "代码", "前十大流通股东", "研报评级分布", "机构调研条数", "取数备注"], inst_rows))
    lines.append("注：公募基金持仓默认不做实时全基金扫描，需接入本地基金持仓缓存或指定报告期后再聚合。")
    lines.append("")

    lines.append("## 7. 资金流向")
    mf_rows = []
    for item in data.get("moneyflow_top") or []:
        mf_rows.append([item.get("name"), item.get("ts_code"), item.get("net_mf_amount")])
    lines.append(_table(["名称", "代码", "主力净流入字段值"], mf_rows))
    lines.append("")

    lines.append("## 8. 明日观察")
    lines.append("- 观察两市成交额是否继续维持高位，若缩量则题材持续性需要打折。")
    lines.append("- 观察成交额 Top 个股是否继续放量承接，尤其是高位强趋势/高位放量分歧个股。")
    lines.append("- 观察行业 Top 是否从少数龙头扩散到板块内部，还是仅个股行情。")
    lines.append("- 观察下跌家数、跌停家数和高位股反馈，判断亏钱效应是否扩大。")
    lines.append("")

    errors = data.get("optional_errors") or {}
    if errors:
        lines.append("## 9. 可选数据取数失败记录")
        lines.append(_table(["接口", "错误"], [[k, v] for k, v in errors.items()]))
        lines.append("")

    lines.append("## 10. 数据说明")
    for note in data.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="latest")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--lookback", type=int, default=30)
    parser.add_argument("--institution-top-n", type=int, default=5)
    parser.add_argument("--institution-lookback-days", type=int, default=365)
    parser.add_argument("--message-id", default="")
    args = parser.parse_args()

    data = _run_fetch(args.date, args.top_n, args.lookback, args.institution_top_n, args.institution_lookback_days)
    md = build_markdown(data)

    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trade_date = data.get("trade_date", "unknown")
    output_path = reports_dir / f"A股今日走势分析_{_slugify(str(trade_date))}_{timestamp}_report.md"
    output_path.write_text(md, encoding="utf-8")
    print(f"已生成报告：{output_path}")
    if args.message_id:
        print(f"message_id: {args.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
