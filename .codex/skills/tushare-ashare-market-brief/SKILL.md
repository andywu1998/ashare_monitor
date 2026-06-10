---
name: tushare-ashare-market-brief
description: Use Tushare SDK to fetch and analyze A-share today's market走势, daily market breadth, index moves, turnover leaders, hot stocks/themes, industry/concept leaders, money flow, and 30-trading-day context. Use when the user asks for A股今日走势分析、A股行情复盘、市场日报、热门题材/行业/概念/成交额Top分析, or asks to use Tushare SDK for A-share market data.
---

# Tushare A-Share Market Brief

## Core Rule

Use Tushare SDK as the primary source. Do not answer “今日走势” from memory. Fetch current/latest trade-date data first, then analyze.

## Data Workflow

1. Resolve latest A-share trade date with `trade_cal`; if the requested date is not open, use the latest previous open day.
2. Fetch today-level market data:
   - indices: `index_daily` for 000001.SH, 399001.SZ, 399006.SZ, 000300.SH, 000905.SH, 000852.SH, 000688.SH when available.
   - whole-market stocks: `daily` + `daily_basic` for latest trade date.
   - breadth: up/down/flat count, >5%, <-5%, limit up/down using pct_chg and `limit_list_d` when available.
   - turnover leaders: rank by `amount` from stock `daily`.
   - money flow: `moneyflow` for latest date if token quota allows.
3. For hot stocks, turnover Top, industry Top, concept/theme Top, add 30-trading-day context:
   - 5/10/20/30-trading-day pct change.
   - latest amount percentile/rank vs 30 days.
   - volume expansion vs 30-day average.
   - position label: low launch, mid-trend, high acceleration, pullback rebound, range-bound, high-volume divergence.
4. For hot stocks, turnover Top, industry Top, concept/theme Top, add institutional context:
   - institutional holdings: latest top float holders, public fund portfolio holdings when available.
   - institutional ratings/research: sell-side rating/earnings forecast reports and research report counts when available.
   - institutional activity: institution research visits and Dragon-Tiger institution seats when available.
   - For sector/theme leaders, aggregate from representative constituent stocks and disclose sample size.
5. For industry/concept/theme analysis:
   - Prefer Tushare interfaces when available for the environment/token.
   - If sector constituent interfaces are unavailable, compute industries from `stock_basic.industry` joined to today stock data.
   - Treat concept/theme data as optional; explicitly say when not fetched due to quota/interface limits.
5. Present analysis in Chinese with concrete numbers and dates.

## Quick Script

Use the bundled script to create a structured JSON package:

```bash
python /home/admin/.codex/skills/tushare-ashare-market-brief/scripts/fetch_ashare_market_brief.py --date latest --top-n 10 --lookback 30 --output /tmp/ashare_market_brief.json
```

Then read the JSON and write the final market analysis. If the script reports failed optional sections, keep the analysis but disclose the missing section.

## Output Structure

Use this order unless the user asks otherwise:

1. 今日总体判断: one paragraph with market state, risk appetite, volume.
2. 指数与量能: index moves and total turnover.
3. 市场广度: up/down count, limit-up/down, large gain/loss distribution.
4. 行业/概念/题材: leaders/laggards and 30-day sustainability.
5. 成交额 Top 个股: today rank plus 30-day position/volume context.
6. 热门股票强弱分层: strong trend, high divergence, rebound, weak names.
7. 资金流向: main money flow if fetched.
8. 明日观察: volume, core stocks, sector continuation, downside breadth.

## Guardrails

- Always include the actual trade date used.
- If today is not a trading day, state the substituted latest trading day.
- Do not overclaim concept/theme data if only industry-level data was computed.
- Do not overclaim institution data: label unavailable sections by interface/permission/quota.
- Respect Tushare rate limits; retry on `频率超限`, but do not spin indefinitely.
- If `TUSHARE_TOKEN` is missing, inspect `~/.zshrc` through the project config pattern or ask the user to configure it.

## References

Read `references/tushare_interfaces.md` only when you need interface names, fields, or fallback rules.
