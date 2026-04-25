"""Plain-text formatter for quick human previews."""

from __future__ import annotations

from datetime import timezone
from typing import List

from ..data_models import DailyDataset, SectorMove


def _format_indices(dataset: DailyDataset) -> List[str]:
    lines = ["指数表现:"]
    for snap in dataset.indices:
        turnover = f"{snap.turnover:.1f} 亿" if snap.turnover is not None else "n/a"
        lines.append(
            f"- {snap.name}({snap.symbol}) {snap.last:.2f} {snap.change:+.2f} ({snap.change_percent:+.2f}%) 成交额 {turnover}"
        )
    return lines


def _format_sectors(title: str, sectors: List[SectorMove]) -> List[str]:
    lines = [title]
    for sector in sectors:
        leaders = f" 领涨:{'/'.join(sector.leaders)}" if sector.leaders else ""
        lines.append(f"- {sector.name} {sector.change_percent:+.2f}%{leaders}")
    return lines


def format_text_report(dataset: DailyDataset) -> str:
    ts = dataset.trade_date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: List[str] = [f"A股日度监控 | 数据时间 {ts}", ""]
    parts.extend(_format_indices(dataset))
    parts.append("")
    parts.append(
        f"市场广度: 涨 {dataset.breadth.advancing} / 跌 {dataset.breadth.declining} / 平 {dataset.breadth.unchanged}, 涨停 {dataset.breadth.limit_up}, 跌停 {dataset.breadth.limit_down}"
    )
    parts.append("")
    parts.extend(_format_sectors("行业领涨:", dataset.top_sectors))
    parts.append("")
    parts.extend(_format_sectors("行业领跌:", dataset.bottom_sectors))
    parts.append("")
    north = dataset.capital_flow.northbound
    main_force = dataset.capital_flow.main_force
    north_text = f"{north:+.2f} 亿" if north is not None else "n/a"
    main_text = f"{main_force:+.2f} 亿" if main_force is not None else "n/a"
    parts.append(f"资金流: 北向 {north_text}, 主力 {main_text}")
    parts.append("")
    parts.append("代表性个股:")
    for stock in dataset.representatives:
        turnover = f"{stock.turnover:.1f} 亿" if stock.turnover is not None else "n/a"
        parts.append(f"- {stock.name}({stock.symbol}) {stock.change_percent:+.2f}% 成交 {turnover}")
    return "\n".join(parts).strip()


__all__ = ["format_text_report"]
