from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Iterable

import pymysql

from .config import MYSQL_CONFIG


def _build_mysql_config(mysql_config: dict | None = None) -> dict:
    cfg = dict(MYSQL_CONFIG)
    runtime_env = {
        "host": os.getenv("MYSQL_HOST"),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
    }
    if os.getenv("MYSQL_PORT"):
        runtime_env["port"] = int(os.getenv("MYSQL_PORT", "3306"))
    cfg.update({k: v for k, v in runtime_env.items() if v not in (None, "")})
    if mysql_config:
        cfg.update({k: v for k, v in mysql_config.items() if v is not None})
    password = str(cfg.get("password") or "").strip()
    if password in {"YOUR_MYSQL_PASSWORD", "YOUR_PASSWORD", "CHANGE_ME"}:
        cfg["password"] = ""
    return cfg


def get_connection(mysql_config: dict | None = None):
    return pymysql.connect(**_build_mysql_config(mysql_config))


def normalize_db_value(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def normalize_db_rows(rows: Iterable[tuple]) -> list[tuple]:
    return [tuple(normalize_db_value(value) for value in row) for row in rows]


def execute_sql_file(path: str) -> None:
    sql_path = Path(path)
    if not sql_path.is_absolute():
        sql_path = Path(__file__).resolve().parent / sql_path

    with sql_path.open("r", encoding="utf-8") as f:
        sql = f.read()

    with get_connection() as conn:
        with conn.cursor() as cursor:
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            # forward-compatible migrations for existing installations
            alters = [
                "ALTER TABLE stock_basic ADD COLUMN asset_type CHAR(1) NOT NULL DEFAULT 'E'",
                "ALTER TABLE stock_basic ADD COLUMN management VARCHAR(128) NULL",
                "ALTER TABLE stock_basic ADD COLUMN custodian VARCHAR(128) NULL",
                "ALTER TABLE stock_basic ADD COLUMN invest_type VARCHAR(64) NULL",
                "ALTER TABLE stock_basic ADD COLUMN fund_type VARCHAR(64) NULL",
                "ALTER TABLE stock_basic ADD COLUMN benchmark VARCHAR(255) NULL",
                "ALTER TABLE stock_basic ADD COLUMN due_date DATE NULL",
                "ALTER TABLE stock_basic ADD COLUMN issue_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN asset_type CHAR(1) NOT NULL DEFAULT 'E'",
                "ALTER TABLE stock_daily ADD COLUMN ann_date DATE NULL",
                "ALTER TABLE stock_daily ADD COLUMN unit_nav DECIMAL(16,6) NULL",
                "ALTER TABLE stock_daily ADD COLUMN accum_nav DECIMAL(16,6) NULL",
                "ALTER TABLE stock_daily ADD COLUMN accum_div DECIMAL(16,6) NULL",
                "ALTER TABLE stock_daily ADD COLUMN net_asset DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN total_netasset DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN adj_nav DECIMAL(16,6) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_sm_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_sm_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_sm_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_sm_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_md_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_md_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_md_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_md_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_lg_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_lg_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_lg_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_lg_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_elg_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN buy_elg_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_elg_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN sell_elg_amount DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN net_mf_vol DECIMAL(20,4) NULL",
                "ALTER TABLE stock_daily ADD COLUMN net_mf_amount DECIMAL(20,4) NULL",
                "CREATE INDEX idx_asset_type_status ON stock_basic (asset_type, list_status)",
                "CREATE INDEX idx_asset_trade_date ON stock_daily (asset_type, trade_date)",
                "CREATE INDEX idx_trade_date_pct_chg ON stock_daily (trade_date, pct_chg)",
                "CREATE INDEX idx_trade_date_net_mf_amount ON stock_daily (trade_date, net_mf_amount)",
                "CREATE INDEX idx_asset_trade_date_net_mf_amount ON stock_daily (asset_type, trade_date, net_mf_amount)",
            ]
            for alter_sql in alters:
                try:
                    cursor.execute(alter_sql)
                except Exception:
                    pass
        conn.commit()


def upsert_stock_daily(rows: Iterable[tuple]) -> int:
    sql = """
    INSERT INTO stock_daily (
        ts_code, trade_date, asset_type, open, high, low, close, pre_close,
        `change`, pct_chg, vol, amount, ann_date, unit_nav, accum_nav,
        accum_div, net_asset, total_netasset, adj_nav,
        buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
        buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
        buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
        buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
        net_mf_vol, net_mf_amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s
    )
    ON DUPLICATE KEY UPDATE
        asset_type = VALUES(asset_type),
        open = VALUES(open),
        high = VALUES(high),
        low = VALUES(low),
        close = VALUES(close),
        pre_close = VALUES(pre_close),
        `change` = VALUES(`change`),
        pct_chg = VALUES(pct_chg),
        vol = VALUES(vol),
        amount = VALUES(amount),
        ann_date = VALUES(ann_date),
        unit_nav = VALUES(unit_nav),
        accum_nav = VALUES(accum_nav),
        accum_div = VALUES(accum_div),
        net_asset = VALUES(net_asset),
        total_netasset = VALUES(total_netasset),
        adj_nav = VALUES(adj_nav),
        buy_sm_vol = VALUES(buy_sm_vol),
        buy_sm_amount = VALUES(buy_sm_amount),
        sell_sm_vol = VALUES(sell_sm_vol),
        sell_sm_amount = VALUES(sell_sm_amount),
        buy_md_vol = VALUES(buy_md_vol),
        buy_md_amount = VALUES(buy_md_amount),
        sell_md_vol = VALUES(sell_md_vol),
        sell_md_amount = VALUES(sell_md_amount),
        buy_lg_vol = VALUES(buy_lg_vol),
        buy_lg_amount = VALUES(buy_lg_amount),
        sell_lg_vol = VALUES(sell_lg_vol),
        sell_lg_amount = VALUES(sell_lg_amount),
        buy_elg_vol = VALUES(buy_elg_vol),
        buy_elg_amount = VALUES(buy_elg_amount),
        sell_elg_vol = VALUES(sell_elg_vol),
        sell_elg_amount = VALUES(sell_elg_amount),
        net_mf_vol = VALUES(net_mf_vol),
        net_mf_amount = VALUES(net_mf_amount)
    """
    normalized_rows = []
    for row in rows:
        if len(row) == 11:
            # legacy stock tuple
            row = (
                row[0], row[1], "E",
                row[2], row[3], row[4], row[5], row[6],
                row[7], row[8], row[9], row[10],
                None, None, None, None, None, None, None,
                None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None, None,
                None, None,
            )
        elif len(row) == 19:
            # current OHLCV/nav tuple, without moneyflow
            row = (
                row[0], row[1], row[2],
                row[3], row[4], row[5], row[6], row[7],
                row[8], row[9], row[10], row[11], row[12], row[13], row[14],
                row[15], row[16], row[17], row[18],
                None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None, None,
                None, None,
            )
        elif len(row) != 37:
            raise ValueError(f"unsupported stock_daily tuple length={len(row)}")
        normalized_rows.append(row)
    data = normalize_db_rows(normalized_rows)
    if not data:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, data)
        conn.commit()
    return len(data)


def upsert_stock_moneyflow(rows: Iterable[tuple]) -> int:
    sql = """
    INSERT INTO stock_daily (
        ts_code, trade_date, asset_type,
        buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
        buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
        buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
        buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
        net_mf_vol, net_mf_amount
    ) VALUES (
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s
    )
    ON DUPLICATE KEY UPDATE
        asset_type = VALUES(asset_type),
        buy_sm_vol = VALUES(buy_sm_vol),
        buy_sm_amount = VALUES(buy_sm_amount),
        sell_sm_vol = VALUES(sell_sm_vol),
        sell_sm_amount = VALUES(sell_sm_amount),
        buy_md_vol = VALUES(buy_md_vol),
        buy_md_amount = VALUES(buy_md_amount),
        sell_md_vol = VALUES(sell_md_vol),
        sell_md_amount = VALUES(sell_md_amount),
        buy_lg_vol = VALUES(buy_lg_vol),
        buy_lg_amount = VALUES(buy_lg_amount),
        sell_lg_vol = VALUES(sell_lg_vol),
        sell_lg_amount = VALUES(sell_lg_amount),
        buy_elg_vol = VALUES(buy_elg_vol),
        buy_elg_amount = VALUES(buy_elg_amount),
        sell_elg_vol = VALUES(sell_elg_vol),
        sell_elg_amount = VALUES(sell_elg_amount),
        net_mf_vol = VALUES(net_mf_vol),
        net_mf_amount = VALUES(net_mf_amount)
    """
    data = normalize_db_rows(rows)
    if not data:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, data)
        conn.commit()
    return len(data)


def upsert_stock_basic(rows: Iterable[tuple]) -> int:
    sql = """
    INSERT INTO stock_basic (
        ts_code, asset_type, symbol, name, area, industry, market,
        exchange, list_status, list_date, delist_date, is_hs,
        management, custodian, invest_type, fund_type, benchmark, due_date, issue_amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s
    )
    ON DUPLICATE KEY UPDATE
        asset_type = VALUES(asset_type),
        symbol = VALUES(symbol),
        name = VALUES(name),
        area = VALUES(area),
        industry = VALUES(industry),
        market = VALUES(market),
        exchange = VALUES(exchange),
        list_status = VALUES(list_status),
        list_date = VALUES(list_date),
        delist_date = VALUES(delist_date),
        is_hs = VALUES(is_hs),
        management = VALUES(management),
        custodian = VALUES(custodian),
        invest_type = VALUES(invest_type),
        fund_type = VALUES(fund_type),
        benchmark = VALUES(benchmark),
        due_date = VALUES(due_date),
        issue_amount = VALUES(issue_amount)
    """
    normalized_rows = []
    for row in rows:
        if len(row) == 11:
            # legacy stock tuple
            row = (
                row[0], "E", row[1], row[2], row[3], row[4], row[5],
                row[6], row[7], row[8], row[9], row[10],
                None, None, None, None, None, None, None,
            )
        normalized_rows.append(row)
    data = normalize_db_rows(normalized_rows)
    if not data:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, data)
        conn.commit()
    return len(data)


def fetch_stock_basic_ts_codes() -> list[str]:
    sql = """
    SELECT ts_code
    FROM stock_basic
    WHERE list_status = 'L'
      AND asset_type = 'E'
    ORDER BY ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return [row[0] for row in cursor.fetchall()]


def fetch_stock_basic_rows() -> list[tuple[str, str, object]]:
    sql = """
    SELECT ts_code, name, list_date
    FROM stock_basic
    WHERE list_status = 'L'
      AND asset_type = 'E'
    ORDER BY ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def fetch_daily_row_counts() -> dict[str, int]:
    sql = """
    SELECT ts_code, COUNT(*) AS row_count
    FROM stock_daily
    WHERE asset_type = 'E'
    GROUP BY ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return {row[0]: int(row[1]) for row in cursor.fetchall()}


def fetch_existing_daily_ts_codes(start_date, end_date) -> set[str]:
    sql = """
    SELECT DISTINCT ts_code
    FROM stock_daily
    WHERE trade_date BETWEEN %s AND %s
      AND asset_type = 'E'
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (start_date, end_date))
            return {row[0] for row in cursor.fetchall()}


def fetch_all_stocks_with_last_trade(mysql_config: dict | None = None) -> list[tuple[str, str, object]]:
    sql = """
    SELECT
        b.ts_code,
        b.name,
        MAX(d.trade_date) AS last_trade_date
    FROM stock_basic b
    LEFT JOIN stock_daily d ON b.ts_code = d.ts_code AND d.asset_type = 'E'
    WHERE b.list_status = 'L'
      AND b.asset_type = 'E'
    GROUP BY b.ts_code, b.name
    ORDER BY b.ts_code
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def fetch_daily_rows_for_stock(
    ts_code: str,
    start_date,
    end_date,
    mysql_config: dict | None = None,
) -> list[tuple]:
    sql = """
    SELECT trade_date, open, high, low, close, pre_close, `change`, pct_chg, vol, amount
    FROM stock_daily
    WHERE ts_code = %s
      AND asset_type = 'E'
      AND trade_date BETWEEN %s AND %s
    ORDER BY trade_date ASC
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (ts_code, start_date, end_date))
            return list(cursor.fetchall())


def upsert_market_breadth_daily_for_range(
    start_date,
    end_date,
    mysql_config: dict | None = None,
) -> int:
    sql = """
    INSERT INTO market_breadth_daily (
        trade_date, total_count, up_count, down_count, flat_count, up_ratio_pct, avg_pct_chg
    )
    SELECT
        trade_date,
        COUNT(*) AS total_count,
        SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) AS up_count,
        SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) AS down_count,
        SUM(CASE WHEN pct_chg = 0 OR pct_chg IS NULL THEN 1 ELSE 0 END) AS flat_count,
        ROUND(100 * SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS up_ratio_pct,
        ROUND(AVG(CASE WHEN pct_chg IS NOT NULL THEN pct_chg END), 4) AS avg_pct_chg
    FROM stock_daily
    WHERE trade_date BETWEEN %s AND %s
      AND asset_type = 'E'
    GROUP BY trade_date
    ON DUPLICATE KEY UPDATE
        total_count = VALUES(total_count),
        up_count = VALUES(up_count),
        down_count = VALUES(down_count),
        flat_count = VALUES(flat_count),
        up_ratio_pct = VALUES(up_ratio_pct),
        avg_pct_chg = VALUES(avg_pct_chg),
        updated_at = CURRENT_TIMESTAMP(3)
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            affected = cursor.execute(sql, (start_date, end_date))
        conn.commit()
    return int(affected or 0)


def fetch_fund_basic_rows() -> list[tuple[str, str, object]]:
    sql = """
    SELECT ts_code, name, list_date
    FROM stock_basic
    WHERE list_status = 'L'
      AND asset_type = 'F'
    ORDER BY ts_code
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def fetch_all_funds_with_last_trade(mysql_config: dict | None = None) -> list[tuple[str, str, object]]:
    sql = """
    SELECT
        b.ts_code,
        b.name,
        MAX(d.trade_date) AS last_trade_date
    FROM stock_basic b
    LEFT JOIN stock_daily d ON b.ts_code = d.ts_code AND d.asset_type = 'F'
    WHERE b.list_status = 'L'
      AND b.asset_type = 'F'
    GROUP BY b.ts_code, b.name
    ORDER BY b.ts_code
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def fetch_daily_rows_for_fund(
    ts_code: str,
    start_date,
    end_date,
    mysql_config: dict | None = None,
) -> list[tuple]:
    sql = """
    SELECT trade_date, open, high, low, close, pre_close, `change`, pct_chg, vol, amount
    FROM stock_daily
    WHERE ts_code = %s
      AND asset_type = 'F'
      AND trade_date BETWEEN %s AND %s
    ORDER BY trade_date ASC
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (ts_code, start_date, end_date))
            return list(cursor.fetchall())
