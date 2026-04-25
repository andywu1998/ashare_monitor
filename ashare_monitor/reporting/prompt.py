"""Prompt builder for Codex summaries."""

from __future__ import annotations

import textwrap
from typing import Iterable

from ..data_models import DailyDataset


def _format_pairs(items: Iterable[str]) -> str:
    return "; ".join(items) if items else "无"


def build_codex_prompt(dataset: DailyDataset) -> str:
    index_lines = [
        f"{snap.name}{snap.change_percent:+.2f}%({snap.change:+.2f})/成交{(snap.turnover or 0):.1f}亿"
        for snap in dataset.indices
    ]
    top_sector_lines = [f"{sec.name}{sec.change_percent:+.2f}%" for sec in dataset.top_sectors]
    bottom_sector_lines = [f"{sec.name}{sec.change_percent:+.2f}%" for sec in dataset.bottom_sectors]
    reps = [f"{stock.name}{stock.change_percent:+.2f}%" for stock in dataset.representatives]

    prompt = textwrap.dedent(
        f"""
        角色：A股策略分析师。请用正式中文总结下列盘面：
        - 指数表现：{_format_pairs(index_lines)}
        - 市场广度：涨{dataset.breadth.advancing}/跌{dataset.breadth.declining}/平{dataset.breadth.unchanged}，涨停{dataset.breadth.limit_up}，跌停{dataset.breadth.limit_down}
        - 行业领涨：{_format_pairs(top_sector_lines)}
        - 行业领跌：{_format_pairs(bottom_sector_lines)}
        - 资金流：北向{dataset.capital_flow.northbound or 0:+.2f}亿，主力{dataset.capital_flow.main_force or 0:+.2f}亿
        - 代表个股：{_format_pairs(reps)}

        要求：
        1. 2-3 句话，依次覆盖“指数+量能”、“行业/主题+资金”、“风险与交易建议”。
        2. 总字数 180 字以内，不要机械罗列原始数字，强调结构性结论。
        3. 如数据不足请明确说明不确定性，禁止输出免责声明。
        """
    ).strip()
    return prompt


__all__ = ["build_codex_prompt"]
