# 港股日线数据源排查记录（2026-04-27）

## 候选数据源清单

1. TuShare Pro `hk_daily`（已有接入）
2. AkShare `stock_hk_hist`（东方财富）
3. AkShare `stock_hk_daily`（新浪）
4. Yahoo Finance（`yfinance` / Yahoo chart & download）
5. Stooq CSV

## 实测结果（按尝试顺序）

| 顺序 | 数据源 | 接口/方式 | 结果 | 结论 |
|---|---|---|---|---|
| 1 | TuShare | `pro.hk_daily` | 被限频（`10次/天`，且有 `2次/分钟`） | 不适合全量回补 |
| 2 | AkShare-东财 | `ak.stock_hk_hist` | 当前网络下持续 `RemoteDisconnected` | 本环境不可用 |
| 3 | AkShare-新浪 | `ak.stock_hk_daily` | 稳定返回历史数据，批量 50/50 成功 | 作为主数据源 |
| 4 | Yahoo Finance | `yfinance.Ticker().history` | `403/429`，被限流 | 当前环境不可用 |
| 5 | Stooq | `https://stooq.com/q/d/l/?s=...&i=d` | 需人工验证码获取 apikey | 不适合无人值守 |

## 当前决策

- 港股全量同步主源切换为 **AkShare-新浪 (`stock_hk_daily`)**。
- 已在同步脚本中实现 `--provider` 参数与 `auto` 回退链路。

---

# 港股估值/财务辅助数据源排查记录（2026-09-16）

> 场景：抓取单只港股「上市以来逐日 PS(TTM)」序列（首个用例：`09660.HK` 地平线机器人）。
> 结论已实现在 `scripts/run_hk_ps_series.py`。

## 实测结果

| 用途 | 数据源 | 接口/方式 | 结果 | 结论 |
|---|---|---|---|---|
| 日线收盘价 | AkShare-新浪 | `ak.stock_hk_daily(symbol="09660")` | 09660 返回 466 个交易日（2024-10-24 上市首日起） | 沿用（与全量同步主源一致） |
| 每日总市值 | 百度股市通 | `https://finance.baidu.com/opendata?query=总市值&market=hk&code=09660&chart_select=全部` | 返回自上市首日起逐日总市值（单位 **亿港元**，含非交易日重复值） | 采用（PS 分子的核心来源） |
| 营业收入 | 东方财富港股财报 | `ak.stock_financial_hk_analysis_indicator_em(symbol, indicator="报告期")` → `OPERATE_INCOME`；`ak.stock_financial_hk_report_em(stock, symbol="利润表", indicator="报告期")` → `营业额` | 两者数值一致；主要指标口径更新更快（已含 2026H1） | 采用（主要指标优先，利润表补齐） |
| 业绩公告发布时点 | 港交所披露易 | `search/prefix.do` 取 `stockId` → `search/titleSearchServlet.do` 按标题过滤 `RESULTS ANNOUNCEMENT` | 拿到官方公告日期时间，可从标题解析报告期（`YEAR ENDED ...` / `SIX MONTHS ENDED ...`） | 采用（TTM 口径切换时点） |
| 港元兑人民币汇率 | 中国银行牌价（AkShare） | `ak.currency_boc_sina(symbol="港币", ...)`，取「央行中间价」，缺省回退「中行折算价」，除以 100 | 2024-10-24 起逐日有值 | 采用 |
| 逐日 PS | 百度股市通 | `query=市销率` | `ResultNum=0`，百度港股无市销率指标 | 不可用，改为自行计算 |
| 逐日 PE/PB/PS | 亿牛网 | `ak.stock_hk_indicator_eniu(symbol="hk09660", indicator="市盈率")` | 返回空表（新上市港股覆盖不全） | 不可用 |
| 股本/公司概况 | 雪球 | `stock.xueqiu.com/v5/stock/f10/hk/*` | 需要登录态，返回 `error_code=400016` | 不可用 |
| 股本变动 | 东方财富 HK F10 | 各版 `PageAjax` / `reportName=RPT_HKF10_*` | 未找到可用「股本结构」接口 | 放弃，改用市值反推 |

## 关键口径

- **PS(TTM) = 当日总市值(港元) ÷ TTM 营业收入(港元)**，其中 `TTM 营业收入(港元) = TTM 营业收入(人民币) ÷ HKDCNY`。
- TTM 以「最近一期**已公告**」的定期报告为准（不是报告期末当日切换）：
  - 最新一期为年报 → `TTM = 该年报营业额`
  - 最新一期为中报 → `TTM = 上年年报 - 上年中报 + 本年中报`（即上年下半年 + 本年上半年）
- 公告在收盘后（≥16:00）发布时，自**下一个交易日**起生效。
- 百度总市值 ÷ 当日收盘价 = 隐含总股本，可用来校验股本变动事件：
  09660 实测台阶为 `13.030B(上市) → 13.200B(2024-11-25 超额配售) → 13.881B(2025-06-23) → 14.652B(2025-10-10) → 14.572B(2026-06-01) → 15.873B(2026-08-03 CARIAD 可转债转股)`，与公开事件一致。
