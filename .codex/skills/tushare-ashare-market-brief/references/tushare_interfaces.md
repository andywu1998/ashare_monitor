# Tushare Interfaces For A-Share Market Brief

## Required

- `trade_cal(exchange='SSE', start_date, end_date, fields='cal_date,is_open,pretrade_date')`: resolve latest open trade date.
- `daily(trade_date=YYYYMMDD)`: stock OHLC, pct_chg, amount.
- `daily_basic(trade_date=YYYYMMDD)`: turnover_rate, volume_ratio, total_mv, circ_mv; use for valuation/liquidity context.
- `stock_basic(exchange='', list_status='L', fields='ts_code,name,industry,market,exchange,list_date')`: names and industry classification.
- `index_daily(ts_code, start_date, end_date)`: index OHLC and pct_chg.

## Useful Optional

- `moneyflow(trade_date=YYYYMMDD)`: main money flow fields, may hit quota.
- `limit_list_d(trade_date=YYYYMMDD)`: limit up/down detail, may depend on token permission.
- `ths_index`, `ths_member`, `ths_daily`: concept/industry theme index and constituents if available.
- `top10_floatholders(ts_code, start_date, end_date)`: latest top 10 float shareholders; use as broad institutional/shareholder holding context.
- `fund_portfolio(ts_code or ann_date/period depending on token/interface version)`: public fund holdings. This is often fund-centric/report-period-centric; prefer a local fund holdings cache for stock-level aggregation instead of scanning all funds live.
- `report_rc(ts_code, start_date, end_date)`: sell-side earnings forecast and rating reports; aggregate recent rating distribution and latest reports.
- `research_report(ts_code, start_date, end_date, report_type='个股研报')`: broker research reports; optional because it may require separate permission.
- `stk_surv(ts_code, start_date, end_date)`: institution research visits; optional and quota-sensitive.
- `top_inst(trade_date, ts_code)`: Dragon-Tiger institution seat transactions for latest trade date; optional and quota-sensitive.

## Fallbacks

- If concept/theme interfaces fail, compute industry leaders from `stock_basic.industry` + stock `daily`.
- If `moneyflow` fails, continue and disclose that moneyflow was unavailable.
- If `limit_list_d` fails, estimate limit up/down from pct_chg thresholds and disclose it is estimated.
- If institutional holding/rating interfaces fail, keep market analysis and include the failed interface in `optional_errors`.
- For industry/concept leaders, avoid querying every constituent by default. Sample top turnover constituents, aggregate available institution fields, and disclose `sample_size`.

## Units

- `daily.amount` is usually in 千元 in Tushare daily; convert to 亿元 by dividing by 100000.
- `moneyflow` amount fields often use 万元; preserve field names and document units in output.
