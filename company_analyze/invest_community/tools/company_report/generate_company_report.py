#!/usr/bin/env python3
"""Generate a company research report using Huoshan LLM API."""

import datetime as _dt
import argparse
import os
import re
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from llm_lib.huoshan import chat_stream


def _call_huoshan_stream(prompt: str, show_progress: bool = True) -> str:
    """
    调用火山引擎LLM流式API生成报告

    Args:
        prompt: 提示词
        show_progress: 是否实时显示生成进度

    Returns:
        完整的生成文本
    """
    system_prompt = (
        "你是一名经验丰富的证券研究员，负责为机构投资者撰写中文证券研究报告。\n"
        "你的首要目标是基于公开信息输出结构化、可验证、证据充分的分析。\n"
        "请严格遵守以下规则：\n"
        "- 不得编造数据、机构观点、持仓变化、目标价、股价表现或来源。\n"
        "- 对无法确认、来源冲突或时效不足的信息，明确标注“未知/需验证”。\n"
        "- 明确区分事实、市场观点与分析推断，不要混写成确定事实。\n"
        "- 关键结论、关键数据、机构观点、持仓变化、估值假设后尽量标注来源与日期。\n"
        "- 先给结论，再展开论证；避免空泛套话和没有依据的乐观/悲观表述。\n"
        "- 输出必须使用中文 Markdown。"
    )

    full_text = []

    if show_progress:
        print("\n开始生成报告...\n")
        print("=" * 60)

    try:
        for chunk in chat_stream(prompt, system_prompt=system_prompt):
            full_text.append(chunk)
            if show_progress:
                print(chunk, end="", flush=True)
    except Exception as e:
        raise RuntimeError(f"调用火山引擎API失败: {e}")

    if show_progress:
        print("\n" + "=" * 60)
        print("\n报告生成完成！\n")

    return "".join(full_text)


def _slugify(name: str) -> str:
    # Keep Unicode letters/digits; replace path separators and illegal filename chars.
    cleaned = name.strip()
    cleaned = re.sub(r"[\\\\/:*?\"<>|]+", "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or "company"


def _build_prompt(company: str) -> str:
    return (
        "请为以下公司撰写一篇面向机构投资者的完整证券研究报告。\n"
        f"公司名称：{company}\n\n"
        "任务目标：\n"
        "- 基于公开信息形成一篇结构完整、证据充分、结论明确的中文研报。\n"
        "- 优先使用最近12个月的信息；对于股价、机构持仓、机构观点等时效性强的信息，优先使用最近90天内可获得的信息。\n"
        "- 对无法确认的信息，必须明确标注“未知/需验证”；不得编造数据、机构观点、目标价或持仓变化。\n\n"
        "研究规则：\n"
        "- 优先使用以下来源：\n"
        "  1. 公司公告、财报、业绩会纪要、交易所披露\n"
        "  2. 主流财经媒体\n"
        "  3. 券商/研究机构公开摘要\n"
        "  4. 基金季报、前十大股东、北向资金、其他可公开验证的机构持仓披露\n"
        "- 区分三类内容：事实、市场观点、推断。\n"
        "- 每个关键判断、关键数据、机构观点、持仓变化、估值假设后，尽量在句末标注来源，格式为：[来源：标题，日期]\n"
        "- 如多个来源存在冲突，明确写出分歧点，不要强行统一口径。\n\n"
        "输出要求：\n"
        "- 使用 Markdown 输出。\n"
        "- 语言：中文。\n"
        "- 风格：专业、克制、清晰，避免空泛表述。\n"
        "- 尽量给出量化信息、时间点、同比/环比变化、估值倍数、区间与敏感性分析。\n"
        "- 先给结论，再展开论证。\n\n"
        "请严格按以下结构输出：\n\n"
        "# 标题\n"
        f"{company} 深度研究报告\n\n"
        "## 1. 核心结论\n"
        "- 投资结论：明确给出“看多 / 中性 / 谨慎”判断。\n"
        "- 核心逻辑：用 3-5 条要点概括。\n"
        "- 关键催化剂：未来 6-12 个月。\n"
        "- 核心风险：用 3-5 条要点概括。\n"
        "- 若信息不足以支持明确结论，直接说明“结论暂不充分，需验证以下关键变量”。\n\n"
        "## 2. 公司概览\n"
        "- 主营业务、收入结构、主要产品/服务、区域分布、客户结构。\n"
        "- 公司所处行业、上市地、证券代码、市值区间（如可得）。\n"
        "- 重要股东或主要股东结构（如可得）。\n\n"
        "## 3. 商业模式与竞争格局\n"
        "- 公司如何赚钱。\n"
        "- 关键收入驱动因素与成本驱动因素。\n"
        "- 竞争对手是谁。\n"
        "- 核心客户是谁。\n"
        "- 上游依赖谁。\n"
        "- 公司护城河/竞争优势是什么。\n"
        "- 是否存在份额提升、价格能力、技术壁垒或渠道优势。\n\n"
        "## 4. 行业景气度与外部环境\n"
        "- 行业所处周期位置。\n"
        "- 需求、供给、价格、政策、技术演进。\n"
        "- 对公司未来 1-2 年的影响。\n"
        "- 若适用，可简要说明行业关键先行指标。\n\n"
        "## 5. 财务分析与关键指标\n"
        "- 最近 3 年及最近报告期的营收、毛利率、归母净利润、现金流、资本开支、负债情况。\n"
        "- 结合业务逻辑解释财务变化，而非只罗列数据。\n"
        "- 指出最重要的 3-5 个财务驱动变量。\n"
        "- 如果适用，补充行业特有指标。\n\n"
        "## 6. 机构持仓与资金关注度\n"
        "- 尽量说明公募基金持仓变化、北向资金变化、前十大股东或重要股东变动、是否为机构重仓/冷门标的。\n"
        "- 说明最近一次可验证披露的时间点。\n"
        "- 如果数据不足，明确写“未知/需验证”。\n"
        "- 这一节尽量用表格输出，包含：指标、最近披露时间、变化方向、备注。\n\n"
        "## 7. 机构分析观点\n"
        "- 汇总主流券商/研究机构的公开观点。\n"
        "- 提炼看多逻辑、看空/分歧点、盈利预测变化、目标价或估值区间（如可得）。\n"
        "- 按“机构 / 日期 / 观点摘要 / 预测或目标价”做表格。\n"
        "- 不得编造机构名称、日期、目标价；查不到就写“未知/需验证”。\n\n"
        "## 8. 估值与假设\n"
        "- 至少使用相对估值（PE / PB / EV/EBITDA / PS 等）或绝对估值（DCF / 分部估值）中的一种；如条件允许，给出两种视角更好。\n"
        "- 明确列出关键假设。\n"
        "- 给出估值区间或合理价值区间。\n"
        "- 若能够支持，给出目标价及其对应的时间维度（例如 6-12 个月）。\n"
        "- 增加简要敏感性分析：若核心变量变化，估值如何变化。\n\n"
        "## 9. 近30天股价表现与事件催化\n"
        "- 查询最近30天股价走势。\n"
        "- 结合成交量、涨跌幅、公告、财报、行业新闻解释股价波动。\n"
        "- 区分“事件驱动”与“基本面驱动”。\n"
        "- 如果无法获得可靠价格数据，明确写“未知/需验证”。\n\n"
        "## 10. 风险提示\n"
        "- 至少列出 5 条风险。\n"
        "- 分为：行业风险、公司经营风险、财务风险、估值风险、政策/监管风险。\n"
        "- 尽量说明每项风险影响的是收入、利润、估值还是现金流。\n\n"
        "## 11. 结论\n"
        "- 用 1 段总结为什么值得关注或为什么应保持谨慎。\n"
        "- 回扣最关键的 2-3 个验证变量。\n\n"
        "## 12. 参考来源\n"
        "- 列出正文使用的主要来源。\n"
        "- 格式：标题 | 来源机构/网站 | 日期。\n\n"
        "额外要求：\n"
        "- 如果关键数据缺失，不要用模糊话术掩盖，直接写“未知/需验证”。\n"
        "- 不要输出空泛套话，例如“公司未来可期”“行业前景广阔”这类没有证据支持的表达。\n"
        "- 如果信息证据不足，请降低结论强度，而不是强行给出确定性判断。\n"
    )


def _build_mock_report(company: str) -> str:
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"# {company} 简易研报（模拟生成）\n\n"
        f"- 生成时间：{timestamp}\n"
        "- 说明：此内容为本地模拟，用于调试流程。\n\n"
        "## 公司概览\n"
        "（模拟内容）公司主营业务、行业位置等。\n\n"
        "## 结论\n"
        "（模拟内容）投资结论与风险提示。\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("company", nargs="?", help="公司名称")
    parser.add_argument("--message-id", dest="message_id", default="", help="飞书 message_id")
    args = parser.parse_args()

    company = (args.company or "").strip()
    if not company:
        company = input("请输入公司名称: ").strip()

    if not company:
        print("未提供公司名称，已退出。")
        return 1

    use_mock = os.getenv("HUOSHAN_MOCK", "0") == "1"
    if use_mock:
        text = _build_mock_report(company)
    else:
        api_key = os.getenv("ARK_API_KEY")
        if not api_key:
            print("缺少 ARK_API_KEY，请先设置环境变量。")
            return 1

        prompt = _build_prompt(company)

        try:
            text = _call_huoshan_stream(prompt, show_progress=True)
        except RuntimeError as exc:
            print(f"API 调用失败：{exc}")
            return 1
        except Exception as exc:
            print(f"发生错误：{exc}")
            return 1

        if not text:
            print("未从 API 响应中获取到文本输出。")
            return 1

    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(company)
    output_path = os.path.join(reports_dir, f"{slug}_{timestamp}_report.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"已生成报告：{output_path}")
    if args.message_id:
        print(f"message_id: {args.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
