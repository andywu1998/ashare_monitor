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
