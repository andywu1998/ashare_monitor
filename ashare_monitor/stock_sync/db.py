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
        conn.commit()


def upsert_stock_daily(rows: Iterable[tuple]) -> int:
    sql = """
    INSERT INTO stock_daily (
        ts_code, trade_date, open, high, low, close, pre_close,
        `change`, pct_chg, vol, amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s
    )
    ON DUPLICATE KEY UPDATE
        open = VALUES(open),
        high = VALUES(high),
        low = VALUES(low),
        close = VALUES(close),
        pre_close = VALUES(pre_close),
        `change` = VALUES(`change`),
        pct_chg = VALUES(pct_chg),
        vol = VALUES(vol),
        amount = VALUES(amount)
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
        ts_code, symbol, name, area, industry, market,
        exchange, list_status, list_date, delist_date, is_hs
    ) VALUES (
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s
    )
    ON DUPLICATE KEY UPDATE
        symbol = VALUES(symbol),
        name = VALUES(name),
        area = VALUES(area),
        industry = VALUES(industry),
        market = VALUES(market),
        exchange = VALUES(exchange),
        list_status = VALUES(list_status),
        list_date = VALUES(list_date),
        delist_date = VALUES(delist_date),
        is_hs = VALUES(is_hs)
    """
    data = normalize_db_rows(rows)
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
    LEFT JOIN stock_daily d ON b.ts_code = d.ts_code
    WHERE b.list_status = 'L'
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
      AND trade_date BETWEEN %s AND %s
    ORDER BY trade_date ASC
    """
    with get_connection(mysql_config=mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (ts_code, start_date, end_date))
            return list(cursor.fetchall())
