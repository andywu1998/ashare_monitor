#!/usr/bin/env python3
"""Cycle analysis web service (backend)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
import json
import math
import os
from html import escape as html_escape
import pymysql
import sys
from threading import Lock
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Path as ApiPath, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[2]  # ashare_monitor/
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ashare_monitor.cycle import find_uptrend_stocks
from ashare_monitor.cycle.fund_screener import find_uptrend_funds
from ashare_monitor.cycle import zigzag_pivots
from services.cycle_web.report_renderer import (
    DEFAULT_TEMPLATE_VERSION,
    build_cycle_payload,
    render_cycle_report_html,
)
from services.cycle_web.suggestions import build_query_variants, suggest_assets


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
SERVICE_REPORT_DIR = BASE_DIR / "reports" / "service"
SERVICE_REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MYSQL_HOST = os.getenv("CYCLE_WEB_MYSQL_HOST", "192.168.1.15")
DEFAULT_MYSQL_USER = os.getenv("CYCLE_WEB_MYSQL_USER", os.getenv("MYSQL_USER", "myuser"))
DEFAULT_MYSQL_DB = os.getenv("CYCLE_WEB_MYSQL_DATABASE", os.getenv("MYSQL_DATABASE", "mydb"))
DEFAULT_MYSQL_PASSWORD = os.getenv("CYCLE_WEB_MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
DEFAULT_MYSQL_PORT = int(os.getenv("CYCLE_WEB_MYSQL_PORT", os.getenv("MYSQL_PORT", "3306")))
UPTREND_TASK_WORKERS = max(1, int(os.getenv("UPTREND_TASK_WORKERS", "2")))
BREADTH_TASK_WORKERS = max(1, int(os.getenv("BREADTH_TASK_WORKERS", "2")))
REPORT_TASK_WORKERS = max(1, int(os.getenv("REPORT_TASK_WORKERS", "2")))
RESEARCH_TASK_WORKERS = max(1, int(os.getenv("RESEARCH_TASK_WORKERS", "2")))
BREADTH_DEFAULT_LOOKBACK_TRADE_DAYS = max(
    30, int(os.getenv("BREADTH_DEFAULT_LOOKBACK_TRADE_DAYS", "180"))
)
BREADTH_FACTOR_LOOKBACK_TRADE_DAYS = max(
    10, int(os.getenv("BREADTH_FACTOR_LOOKBACK_TRADE_DAYS", "40"))
)
BREADTH_MAX_SAMPLE_POINTS = max(50, int(os.getenv("BREADTH_MAX_SAMPLE_POINTS", "300")))
BREADTH_CACHE_TTL_SEC = max(30, int(os.getenv("BREADTH_CACHE_TTL_SEC", "600")))
BREADTH_AUTO_ASYNC_SPAN_DAYS = max(60, int(os.getenv("BREADTH_AUTO_ASYNC_SPAN_DAYS", "220")))

UPTREND_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=UPTREND_TASK_WORKERS)
UPTREND_TABLE_LOCK = Lock()
UPTREND_TABLE_READY: set[tuple[str, int, str, str]] = set()
BREADTH_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=BREADTH_TASK_WORKERS)
BREADTH_TABLE_LOCK = Lock()
BREADTH_TABLE_READY: set[tuple[str, int, str, str]] = set()
REPORT_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=REPORT_TASK_WORKERS)
REPORT_TABLE_LOCK = Lock()
REPORT_TABLE_READY: set[tuple[str, int, str, str]] = set()
RESEARCH_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=RESEARCH_TASK_WORKERS)
RESEARCH_TABLE_LOCK = Lock()
RESEARCH_TABLE_READY: set[tuple[str, int, str, str]] = set()
CYCLE_EVENT_TABLE_LOCK = Lock()
CYCLE_EVENT_TABLE_READY: set[tuple[str, int, str, str]] = set()
BREADTH_CACHE_LOCK = Lock()
BREADTH_RESULT_CACHE: dict[str, tuple[float, str]] = {}
REPORT_CACHE_LOCK = Lock()
REPORT_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
REPORT_CACHE_TTL_SEC = max(30, int(os.getenv("REPORT_CACHE_TTL_SEC", "1800")))


def _normalize_password(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    placeholders = {"YOUR_MYSQL_PASSWORD", "YOUR_PASSWORD", "CHANGE_ME"}
    if not value or value in placeholders:
        return ""
    return value


def _jsonify_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _jsonify_value(v) for k, v in row.items()}


def _report_cache_key(
    *,
    mysql_cfg: dict,
    asset_type: str,
    ts_code: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    output_format: str,
    template_version: str,
) -> str:
    return "|".join(
        [
            str(mysql_cfg.get("host", "")),
            str(mysql_cfg.get("port", "")),
            str(mysql_cfg.get("database", "")),
            str(asset_type),
            str(ts_code),
            f"{float(threshold):.6f}",
            str(int(min_gap)),
            str(int(lookback_days)),
            str(end_date or ""),
            str(output_format or "html"),
            str(template_version or DEFAULT_TEMPLATE_VERSION),
        ]
    )


def _report_cache_get(key: str) -> Optional[dict]:
    now = time.time()
    with REPORT_CACHE_LOCK:
        item = REPORT_RESULT_CACHE.get(key)
        if not item:
            return None
        expire_at, payload = item
        if now > expire_at:
            del REPORT_RESULT_CACHE[key]
            return None
        return dict(payload)


def _report_cache_set(key: str, payload: dict) -> None:
    with REPORT_CACHE_LOCK:
        REPORT_RESULT_CACHE[key] = (time.time() + REPORT_CACHE_TTL_SEC, dict(payload))


def _insert_report_task(
    *,
    task_id: str,
    status: str,
    asset_type: str,
    ts_code: Optional[str],
    query_name: Optional[str],
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    output_format: str,
    template_version: str,
    mysql_cfg: dict,
) -> None:
    _ensure_report_task_tables(mysql_cfg)
    sql = """
    INSERT INTO report_gen_task (
      task_id, status, asset_type, ts_code, query_name, threshold, min_gap, lookback_days, end_date,
      output_format, template_version
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    task_id,
                    status,
                    asset_type,
                    ts_code,
                    (query_name or "")[:128] or None,
                    threshold,
                    min_gap,
                    lookback_days,
                    end_date,
                    output_format,
                    template_version,
                ),
            )
        conn.commit()


def _mark_report_task_running(task_id: str, mysql_cfg: dict) -> None:
    _ensure_report_task_tables(mysql_cfg)
    sql = """
    UPDATE report_gen_task
    SET status='running', started_at=NOW(3), updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
        conn.commit()


def _mark_report_task_done(task_id: str, result: dict, duration_ms: int, mysql_cfg: dict) -> None:
    _ensure_report_task_tables(mysql_cfg)
    sql = """
    UPDATE report_gen_task
    SET status='done',
        report_id=%s,
        cache_hit=%s,
        duration_ms=%s,
        error_message=NULL,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    result.get("report_id"),
                    1 if result.get("cache_hit") else 0,
                    max(0, int(duration_ms)),
                    task_id,
                ),
            )
        conn.commit()


def _mark_report_task_error(task_id: str, error_message: str, duration_ms: int, mysql_cfg: dict) -> None:
    _ensure_report_task_tables(mysql_cfg)
    sql = """
    UPDATE report_gen_task
    SET status='error',
        error_message=%s,
        duration_ms=%s,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, ((error_message or "")[:4096], max(0, int(duration_ms)), task_id))
        conn.commit()


def _fetch_report_task(task_id: str, mysql_cfg: dict) -> Optional[dict]:
    _ensure_report_task_tables(mysql_cfg)
    sql = """
    SELECT task_id, status, asset_type, ts_code, query_name, threshold, min_gap, lookback_days, end_date,
           output_format, template_version, report_id, cache_hit, error_message, created_at, started_at, finished_at, duration_ms
    FROM report_gen_task
    WHERE task_id = %s
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            row = cursor.fetchone()
    return _jsonify_row(row) if row else None


def _resolve_mysql_cfg(
    *,
    mysql_host: str,
    mysql_user: str,
    mysql_database: str,
    mysql_password: Optional[str],
    mysql_port: int = DEFAULT_MYSQL_PORT,
) -> dict:
    password = _normalize_password(mysql_password) or _normalize_password(DEFAULT_MYSQL_PASSWORD)
    if not password:
        raise HTTPException(
            status_code=400,
            detail="mysql password missing; pass mysql_password or set MYSQL_PASSWORD env",
        )
    return {
        "host": mysql_host,
        "port": int(mysql_port),
        "user": mysql_user,
        "password": password,
        "database": mysql_database,
    }


def _mysql_connect(mysql_cfg: dict):
    return pymysql.connect(
        host=mysql_cfg["host"],
        port=int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        user=mysql_cfg["user"],
        password=mysql_cfg["password"],
        database=mysql_cfg["database"],
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_uptrend_tables(mysql_cfg: dict) -> None:
    key = (
        str(mysql_cfg["host"]),
        int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        str(mysql_cfg["user"]),
        str(mysql_cfg["database"]),
    )
    with UPTREND_TABLE_LOCK:
        if key in UPTREND_TABLE_READY:
            return

    task_sql = """
    CREATE TABLE IF NOT EXISTS uptrend_scan_task (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      task_id VARCHAR(32) NOT NULL,
      asset_type CHAR(1) NOT NULL DEFAULT 'E',
      status VARCHAR(16) NOT NULL,
      threshold DECIMAL(10,4) NOT NULL,
      min_gap INT NOT NULL,
      lookback_days INT NOT NULL,
      min_rows INT NOT NULL,
      top_k INT NOT NULL,
      end_date DATE NULL,
      request_ip VARCHAR(64) NULL,
      user_agent VARCHAR(255) NULL,
      result_count INT NULL,
      returned_count INT NULL,
      error_message TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      started_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      duration_ms INT NULL,
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_task_id (task_id),
      KEY idx_asset_created (asset_type, created_at),
      KEY idx_status_created (status, created_at),
      KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    item_sql = """
    CREATE TABLE IF NOT EXISTS uptrend_scan_task_item (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      task_id VARCHAR(32) NOT NULL,
      rank_no INT NOT NULL,
      ts_code VARCHAR(16) NOT NULL,
      name VARCHAR(64) NOT NULL,
      last_trade_date DATE NULL,
      last_close DECIMAL(16,4) NULL,
      pivot_count INT NULL,
      cycle_count INT NULL,
      since_last_pivot_days INT NULL,
      latest_cycle_chg_pct DECIMAL(10,2) NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_task_rank (task_id, rank_no),
      KEY idx_task_id (task_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(task_sql)
            cursor.execute(item_sql)
            for alter_sql in (
                "ALTER TABLE uptrend_scan_task ADD COLUMN asset_type CHAR(1) NOT NULL DEFAULT 'E'",
                "CREATE INDEX idx_asset_created ON uptrend_scan_task (asset_type, created_at)",
            ):
                try:
                    cursor.execute(alter_sql)
                except Exception:
                    pass
        conn.commit()

    with UPTREND_TABLE_LOCK:
        UPTREND_TABLE_READY.add(key)


def _ensure_report_task_tables(mysql_cfg: dict) -> None:
    key = (
        str(mysql_cfg["host"]),
        int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        str(mysql_cfg["user"]),
        str(mysql_cfg["database"]),
    )
    with REPORT_TABLE_LOCK:
        if key in REPORT_TABLE_READY:
            return

    task_sql = """
    CREATE TABLE IF NOT EXISTS report_gen_task (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      task_id VARCHAR(32) NOT NULL,
      status VARCHAR(16) NOT NULL,
      asset_type CHAR(1) NOT NULL DEFAULT 'E',
      ts_code VARCHAR(16) NULL,
      query_name VARCHAR(128) NULL,
      threshold DECIMAL(10,4) NOT NULL,
      min_gap INT NOT NULL,
      lookback_days INT NOT NULL,
      end_date DATE NULL,
      output_format VARCHAR(8) NOT NULL,
      template_version VARCHAR(32) NOT NULL,
      report_id VARCHAR(32) NULL,
      cache_hit TINYINT(1) NOT NULL DEFAULT 0,
      error_message TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      started_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      duration_ms INT NULL,
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_task_id (task_id),
      KEY idx_status_created (status, created_at),
      KEY idx_asset_created (asset_type, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(task_sql)
        conn.commit()

    with REPORT_TABLE_LOCK:
        REPORT_TABLE_READY.add(key)


def _ensure_cycle_event_tables(mysql_cfg: dict) -> None:
    key = (
        str(mysql_cfg["host"]),
        int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        str(mysql_cfg["user"]),
        str(mysql_cfg["database"]),
    )
    with CYCLE_EVENT_TABLE_LOCK:
        if key in CYCLE_EVENT_TABLE_READY:
            return

    event_sql = """
    CREATE TABLE IF NOT EXISTS cycle_event (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      event_uuid CHAR(32) NOT NULL,
      trade_date DATE NOT NULL,
      title VARCHAR(120) NOT NULL,
      content TEXT NOT NULL,
      is_global TINYINT(1) NOT NULL DEFAULT 0,
      tags VARCHAR(255) NULL,
      source VARCHAR(32) NOT NULL DEFAULT 'manual',
      created_by VARCHAR(64) NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_event_uuid (event_uuid),
      KEY idx_trade_date (trade_date),
      KEY idx_global_trade_date (is_global, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    map_sql = """
    CREATE TABLE IF NOT EXISTS cycle_event_asset (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      event_uuid CHAR(32) NOT NULL,
      ts_code VARCHAR(16) NOT NULL,
      asset_type CHAR(1) NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_event_asset (event_uuid, ts_code),
      KEY idx_ts_code (ts_code),
      KEY idx_event_uuid (event_uuid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(event_sql)
            cursor.execute(map_sql)
        conn.commit()

    with CYCLE_EVENT_TABLE_LOCK:
        CYCLE_EVENT_TABLE_READY.add(key)


def _resolve_event_trade_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="trade_date is required")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="trade_date must be YYYY-MM-DD") from exc
    return parsed.isoformat()


def _normalize_event_ts_codes(ts_codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for code in ts_codes or []:
        safe = str(code or "").strip().upper()
        if not safe or safe in seen:
            continue
        seen.add(safe)
        out.append(safe)
    return out


def _fetch_event_asset_type_map(ts_codes: list[str], mysql_cfg: dict) -> dict[str, str]:
    if not ts_codes:
        return {}
    marks = ",".join(["%s"] * len(ts_codes))
    sql = f"""
    SELECT ts_code, asset_type
    FROM stock_basic
    WHERE ts_code IN ({marks})
      AND list_status = 'L'
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(ts_codes))
            rows = cursor.fetchall() or []
    out: dict[str, str] = {}
    for row in rows:
        code = str(row.get("ts_code") or "").upper()
        typ = str(row.get("asset_type") or "").upper()[:1]
        if code:
            out[code] = typ or "E"
    return out


def _create_cycle_event(
    *,
    trade_date: str,
    title: str,
    content: str,
    ts_codes: list[str],
    is_global: bool,
    tags: Optional[str],
    source: str,
    created_by: str,
    mysql_cfg: dict,
) -> dict:
    _ensure_cycle_event_tables(mysql_cfg)
    safe_title = (title or "").strip()
    safe_content = (content or "").strip()
    if not safe_title:
        raise HTTPException(status_code=400, detail="title is required")
    if not safe_content:
        raise HTTPException(status_code=400, detail="content is required")
    safe_trade_date = _resolve_event_trade_date(trade_date)
    safe_codes = _normalize_event_ts_codes(ts_codes)
    safe_source = (source or "manual").strip()[:32] or "manual"
    safe_tags = (tags or "").strip()[:255] or None
    safe_creator = (created_by or "").strip()[:64] or None
    if (not is_global) and (not safe_codes):
        raise HTTPException(status_code=400, detail="ts_codes required when global_event=false")

    asset_type_map = _fetch_event_asset_type_map(safe_codes, mysql_cfg)
    unknown = [c for c in safe_codes if c not in asset_type_map]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown ts_code(s): {','.join(unknown[:8])}")

    event_uuid = uuid.uuid4().hex
    event_sql = """
    INSERT INTO cycle_event (
      event_uuid, trade_date, title, content, is_global, tags, source, created_by
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    map_sql = """
    INSERT INTO cycle_event_asset (event_uuid, ts_code, asset_type)
    VALUES (%s, %s, %s)
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                event_sql,
                (
                    event_uuid,
                    safe_trade_date,
                    safe_title[:120],
                    safe_content[:4000],
                    1 if is_global else 0,
                    safe_tags,
                    safe_source,
                    safe_creator,
                ),
            )
            for code in safe_codes:
                cursor.execute(map_sql, (event_uuid, code, asset_type_map.get(code) or "E"))
        conn.commit()

    return {
        "event_uuid": event_uuid,
        "trade_date": safe_trade_date,
        "title": safe_title[:120],
        "content": safe_content[:4000],
        "global_event": bool(is_global),
        "tags": safe_tags,
        "source": safe_source,
        "ts_codes": safe_codes,
    }


def _list_cycle_events(
    *,
    ts_code: str,
    asset_type: str,
    start_date: str,
    end_date: str,
    mysql_cfg: dict,
) -> list[dict]:
    _ensure_cycle_event_tables(mysql_cfg)
    safe_code = (ts_code or "").strip().upper()
    safe_type = (asset_type or "").strip().upper()[:1]
    sql = """
    SELECT e.event_uuid, e.trade_date, e.title, e.content, e.is_global, e.tags, e.source, e.created_at,
           (
             SELECT GROUP_CONCAT(DISTINCT aa.ts_code ORDER BY aa.ts_code SEPARATOR ',')
             FROM cycle_event_asset aa
             WHERE aa.event_uuid = e.event_uuid
           ) AS ts_codes
    FROM cycle_event e
    WHERE e.trade_date BETWEEN %s AND %s
      AND (
        e.is_global = 1
        OR EXISTS (
          SELECT 1
          FROM cycle_event_asset x
          WHERE x.event_uuid = e.event_uuid
            AND x.ts_code = %s
        )
      )
    ORDER BY e.trade_date ASC, e.created_at ASC
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (start_date, end_date, safe_code))
            rows = cursor.fetchall() or []

    out: list[dict] = []
    for row in rows:
        codes = [x for x in str(row.get("ts_codes") or "").split(",") if x]
        out.append(
            {
                "event_uuid": str(row.get("event_uuid") or ""),
                "trade_date": str(row.get("trade_date") or ""),
                "title": str(row.get("title") or ""),
                "content": str(row.get("content") or ""),
                "global_event": bool(row.get("is_global")),
                "tags": row.get("tags"),
                "source": row.get("source"),
                "ts_codes": codes,
                "asset_type": safe_type or None,
                "created_at": _jsonify_value(row.get("created_at")),
            }
        )
    return out


def _delete_cycle_event(*, event_uuid: str, mysql_cfg: dict) -> bool:
    _ensure_cycle_event_tables(mysql_cfg)
    safe_uuid = str(event_uuid or "").strip().lower()
    if not safe_uuid:
        raise HTTPException(status_code=400, detail="event_uuid is required")
    if len(safe_uuid) != 32 or any(ch not in "0123456789abcdef" for ch in safe_uuid):
        raise HTTPException(status_code=400, detail="event_uuid must be 32-char hex")

    del_map_sql = "DELETE FROM cycle_event_asset WHERE event_uuid = %s"
    del_event_sql = "DELETE FROM cycle_event WHERE event_uuid = %s"
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(del_map_sql, (safe_uuid,))
            cursor.execute(del_event_sql, (safe_uuid,))
            affected = int(cursor.rowcount or 0)
        conn.commit()
    return affected > 0


def _ensure_market_breadth_tables(mysql_cfg: dict) -> None:
    key = (
        str(mysql_cfg["host"]),
        int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        str(mysql_cfg["user"]),
        str(mysql_cfg["database"]),
    )
    with BREADTH_TABLE_LOCK:
        if key in BREADTH_TABLE_READY:
            return

    idx_sql = (
        "CREATE INDEX idx_trade_date_pct_chg "
        "ON stock_daily (trade_date, pct_chg)"
    )
    summary_sql = """
    CREATE TABLE IF NOT EXISTS market_breadth_daily (
      trade_date DATE NOT NULL PRIMARY KEY,
      total_count INT NOT NULL,
      up_count INT NOT NULL,
      down_count INT NOT NULL,
      flat_count INT NOT NULL,
      up_ratio_pct DECIMAL(8,2) NOT NULL,
      avg_pct_chg DECIMAL(10,4) NOT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      KEY idx_updated_at (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    task_sql = """
    CREATE TABLE IF NOT EXISTS market_breadth_task (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      task_id VARCHAR(32) NOT NULL,
      status VARCHAR(16) NOT NULL,
      start_date DATE NOT NULL,
      end_date DATE NOT NULL,
      with_report TINYINT(1) NOT NULL DEFAULT 0,
      sample_points INT NOT NULL DEFAULT 300,
      request_ip VARCHAR(64) NULL,
      user_agent VARCHAR(255) NULL,
      row_count INT NULL,
      report_id VARCHAR(32) NULL,
      result_file VARCHAR(255) NULL,
      cache_hit TINYINT(1) NOT NULL DEFAULT 0,
      query_ms INT NULL,
      render_ms INT NULL,
      total_ms INT NULL,
      error_message TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      started_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_task_id (task_id),
      KEY idx_status_created (status, created_at),
      KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(idx_sql)
            except Exception:
                # index already exists
                pass
            cursor.execute(summary_sql)
            cursor.execute(task_sql)
            for alter_sql in (
                "ALTER TABLE market_breadth_task ADD COLUMN result_file VARCHAR(255) NULL",
                "ALTER TABLE market_breadth_task ADD COLUMN cache_hit TINYINT(1) NOT NULL DEFAULT 0",
                "ALTER TABLE market_breadth_task ADD COLUMN query_ms INT NULL",
                "ALTER TABLE market_breadth_task ADD COLUMN render_ms INT NULL",
                "ALTER TABLE market_breadth_task ADD COLUMN total_ms INT NULL",
            ):
                try:
                    cursor.execute(alter_sql)
                except Exception:
                    pass
        conn.commit()

    with BREADTH_TABLE_LOCK:
        BREADTH_TABLE_READY.add(key)


def _sync_market_breadth_daily(mysql_cfg: dict, start_date: str, end_date: str) -> None:
    _ensure_market_breadth_tables(mysql_cfg)
    start_dt = date.fromisoformat(start_date)
    end_dt = date.fromisoformat(end_date)
    if start_dt > end_dt:
        return

    range_sql = """
    SELECT MIN(trade_date) AS min_trade, MAX(trade_date) AS max_trade
    FROM market_breadth_daily
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(range_sql)
            synced = cursor.fetchone() or {}
    min_synced = synced.get("min_trade")
    max_synced = synced.get("max_trade")

    sync_ranges: list[tuple[date, date]] = []
    if not min_synced or not max_synced:
        sync_ranges.append((start_dt, end_dt))
    else:
        if start_dt < min_synced:
            sync_ranges.append((start_dt, min(end_dt, min_synced - timedelta(days=1))))
        if end_dt > max_synced:
            tail_start = max(start_dt, max_synced - timedelta(days=7))
            sync_ranges.append((tail_start, end_dt))

    if not sync_ranges:
        return

    upsert_sql = """
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
      updated_at = CURRENT_TIMESTAMP(3);
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            for sync_start, sync_end in sync_ranges:
                if sync_start > sync_end:
                    continue
                cursor.execute(upsert_sql, (sync_start.isoformat(), sync_end.isoformat()))
        conn.commit()


def _insert_uptrend_task(
    *,
    task_id: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    asset_type: str,
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> None:
    _ensure_uptrend_tables(mysql_cfg)
    sql = """
    INSERT INTO uptrend_scan_task (
      task_id, asset_type, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
      request_ip, user_agent
    ) VALUES (
      %s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    task_id,
                    asset_type,
                    threshold,
                    min_gap,
                    lookback_days,
                    min_rows,
                    top_k,
                    end_date or None,
                    (request_ip or "")[:64] or None,
                    (user_agent or "")[:255] or None,
                ),
            )
        conn.commit()


def _mark_uptrend_task_running(task_id: str, mysql_cfg: dict) -> None:
    _ensure_uptrend_tables(mysql_cfg)
    sql = """
    UPDATE uptrend_scan_task
    SET status = 'running', started_at = NOW(3), updated_at = NOW(3)
    WHERE task_id = %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
        conn.commit()


def _mark_uptrend_task_done(task_id: str, result: dict, duration_ms: int, mysql_cfg: dict) -> None:
    _ensure_uptrend_tables(mysql_cfg)
    update_sql = """
    UPDATE uptrend_scan_task
    SET status = 'done',
        result_count = %s,
        returned_count = %s,
        duration_ms = %s,
        error_message = NULL,
        finished_at = NOW(3),
        updated_at = NOW(3)
    WHERE task_id = %s
    """
    delete_sql = "DELETE FROM uptrend_scan_task_item WHERE task_id = %s"
    insert_sql = """
    INSERT INTO uptrend_scan_task_item (
      task_id, rank_no, ts_code, name, last_trade_date, last_close,
      pivot_count, cycle_count, since_last_pivot_days, latest_cycle_chg_pct
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = result.get("items") or []
    payload = []
    for idx, row in enumerate(rows, start=1):
        payload.append(
            (
                task_id,
                idx,
                row.get("ts_code"),
                row.get("name"),
                row.get("last_trade_date"),
                row.get("last_close"),
                row.get("pivot_count"),
                row.get("cycle_count"),
                row.get("since_last_pivot_days"),
                row.get("latest_cycle_chg_pct"),
            )
        )
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                update_sql,
                (
                    int(result.get("count") or 0),
                    int(result.get("returned") or 0),
                    max(0, int(duration_ms)),
                    task_id,
                ),
            )
            cursor.execute(delete_sql, (task_id,))
            if payload:
                cursor.executemany(insert_sql, payload)
        conn.commit()


def _mark_uptrend_task_error(task_id: str, error_message: str, duration_ms: int, mysql_cfg: dict) -> None:
    _ensure_uptrend_tables(mysql_cfg)
    sql = """
    UPDATE uptrend_scan_task
    SET status = 'error',
        error_message = %s,
        duration_ms = %s,
        finished_at = NOW(3),
        updated_at = NOW(3)
    WHERE task_id = %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, ((error_message or "")[:4096], max(0, int(duration_ms)), task_id))
        conn.commit()


def _fetch_uptrend_task(task_id: str, mysql_cfg: dict) -> Optional[dict]:
    _ensure_uptrend_tables(mysql_cfg)
    sql = """
    SELECT
      task_id, asset_type, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
      result_count, returned_count, error_message, created_at, started_at, finished_at, duration_ms, updated_at
    FROM uptrend_scan_task
    WHERE task_id = %s
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            row = cursor.fetchone()
    return _jsonify_row(row) if row else None


def _fetch_uptrend_task_items(
    *,
    task_id: str,
    page: int,
    page_size: int,
    mysql_cfg: dict,
) -> tuple[int, list[dict]]:
    _ensure_uptrend_tables(mysql_cfg)
    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 1000))
    offset = (safe_page - 1) * safe_size
    count_sql = "SELECT COUNT(*) AS total FROM uptrend_scan_task_item WHERE task_id = %s"
    list_sql = """
    SELECT
      rank_no, ts_code, name, last_trade_date, last_close, pivot_count,
      cycle_count, since_last_pivot_days, latest_cycle_chg_pct
    FROM uptrend_scan_task_item
    WHERE task_id = %s
    ORDER BY rank_no ASC
    LIMIT %s OFFSET %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, (task_id,))
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(list_sql, (task_id, safe_size, offset))
            rows = cursor.fetchall() or []
    return total, [_jsonify_row(r) for r in rows]


def _list_uptrend_tasks(
    *,
    page: int,
    page_size: int,
    status: str,
    asset_type: str,
    mysql_cfg: dict,
) -> tuple[int, list[dict]]:
    _ensure_uptrend_tables(mysql_cfg)
    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 100))
    offset = (safe_page - 1) * safe_size
    where = ""
    args: list[Any] = []
    where_clauses = []
    if status:
        where_clauses.append("status = %s")
        args.append(status)
    if asset_type:
        where_clauses.append("asset_type = %s")
        args.append(asset_type)
    if where_clauses:
        where = "WHERE " + " AND ".join(where_clauses)
    count_sql = f"SELECT COUNT(*) AS total FROM uptrend_scan_task {where}"
    list_sql = f"""
    SELECT
      task_id, asset_type, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
      result_count, returned_count, error_message, created_at, started_at, finished_at, duration_ms
    FROM uptrend_scan_task
    {where}
    ORDER BY created_at DESC
    LIMIT %s OFFSET %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, tuple(args))
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(list_sql, tuple(args + [safe_size, offset]))
            rows = cursor.fetchall() or []
    return total, [_jsonify_row(r) for r in rows]


def _insert_breadth_task(
    *,
    task_id: str,
    start_date: str,
    end_date: str,
    with_report: bool,
    sample_points: int,
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> None:
    _ensure_market_breadth_tables(mysql_cfg)
    sql = """
    INSERT INTO market_breadth_task (
      task_id, status, start_date, end_date, with_report, sample_points, request_ip, user_agent
    ) VALUES (
      %s, 'queued', %s, %s, %s, %s, %s, %s
    )
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    task_id,
                    start_date,
                    end_date,
                    1 if with_report else 0,
                    max(1, sample_points),
                    (request_ip or "")[:64] or None,
                    (user_agent or "")[:255] or None,
                ),
            )
        conn.commit()


def _mark_breadth_task_running(task_id: str, mysql_cfg: dict) -> None:
    _ensure_market_breadth_tables(mysql_cfg)
    sql = """
    UPDATE market_breadth_task
    SET status='running', started_at=NOW(3), updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
        conn.commit()


def _mark_breadth_task_done(task_id: str, payload: dict, metrics: dict, mysql_cfg: dict) -> None:
    _ensure_market_breadth_tables(mysql_cfg)
    result_file = _save_breadth_task_result(task_id, payload)
    sql = """
    UPDATE market_breadth_task
    SET status='done',
        row_count=%s,
        report_id=%s,
        result_file=%s,
        cache_hit=%s,
        query_ms=%s,
        render_ms=%s,
        total_ms=%s,
        error_message=NULL,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    int(payload.get("count") or 0),
                    payload.get("report_id"),
                    result_file,
                    1 if metrics.get("cache_hit") else 0,
                    int(metrics.get("query_ms") or 0),
                    int(metrics.get("render_ms") or 0),
                    int(metrics.get("total_ms") or 0),
                    task_id,
                ),
            )
        conn.commit()


def _mark_breadth_task_error(task_id: str, error_message: str, total_ms: int, mysql_cfg: dict) -> None:
    _ensure_market_breadth_tables(mysql_cfg)
    sql = """
    UPDATE market_breadth_task
    SET status='error',
        error_message=%s,
        total_ms=%s,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, ((error_message or "")[:4096], max(0, int(total_ms)), task_id))
        conn.commit()


def _fetch_breadth_task(task_id: str, mysql_cfg: dict) -> Optional[dict]:
    _ensure_market_breadth_tables(mysql_cfg)
    sql = """
    SELECT
      task_id, status, start_date, end_date, with_report, sample_points, row_count, report_id, result_file,
      cache_hit, query_ms, render_ms, total_ms, error_message, created_at, started_at, finished_at, updated_at
    FROM market_breadth_task
    WHERE task_id = %s
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            row = cursor.fetchone()
    return _jsonify_row(row) if row else None


def _list_breadth_tasks(
    *,
    page: int,
    page_size: int,
    status: str,
    mysql_cfg: dict,
) -> tuple[int, list[dict]]:
    _ensure_market_breadth_tables(mysql_cfg)
    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 100))
    offset = (safe_page - 1) * safe_size
    where = ""
    args: list[Any] = []
    if status:
        where = "WHERE status=%s"
        args.append(status)
    count_sql = f"SELECT COUNT(*) AS total FROM market_breadth_task {where}"
    list_sql = f"""
    SELECT
      task_id, status, start_date, end_date, with_report, sample_points, row_count, report_id,
      cache_hit, query_ms, render_ms, total_ms, error_message, created_at, started_at, finished_at
    FROM market_breadth_task
    {where}
    ORDER BY created_at DESC
    LIMIT %s OFFSET %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, tuple(args))
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(list_sql, tuple(args + [safe_size, offset]))
            rows = cursor.fetchall() or []
    return total, [_jsonify_row(r) for r in rows]


def _ensure_research_task_tables(mysql_cfg: dict) -> None:
    key = (
        str(mysql_cfg["host"]),
        int(mysql_cfg.get("port", DEFAULT_MYSQL_PORT)),
        str(mysql_cfg["user"]),
        str(mysql_cfg["database"]),
    )
    with RESEARCH_TABLE_LOCK:
        if key in RESEARCH_TABLE_READY:
            return

    task_sql = """
    CREATE TABLE IF NOT EXISTS factor_research_task (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      task_id VARCHAR(32) NOT NULL,
      task_type VARCHAR(32) NOT NULL,
      status VARCHAR(16) NOT NULL,
      start_date DATE NOT NULL,
      end_date DATE NOT NULL,
      top_k INT NULL,
      min_list_days INT NULL,
      parameter_trials INT NULL,
      commission_bps DECIMAL(10,4) NULL,
      slippage_bps DECIMAL(10,4) NULL,
      row_count INT NULL,
      result_file VARCHAR(255) NULL,
      query_ms INT NULL,
      total_ms INT NULL,
      error_message TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      started_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_task_id (task_id),
      KEY idx_type_created (task_type, created_at),
      KEY idx_status_created (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(task_sql)
            for alter_sql in (
                "ALTER TABLE factor_research_task ADD COLUMN query_ms INT NULL",
                "ALTER TABLE factor_research_task ADD COLUMN total_ms INT NULL",
                "ALTER TABLE factor_research_task MODIFY COLUMN task_type VARCHAR(32) NOT NULL",
            ):
                try:
                    cursor.execute(alter_sql)
                except Exception:
                    pass
        conn.commit()

    with RESEARCH_TABLE_LOCK:
        RESEARCH_TABLE_READY.add(key)


def _save_research_task_result(task_id: str, payload: dict) -> str:
    name = f"factor_research_task_{task_id}.json"
    path = SERVICE_REPORT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return name


def _load_research_task_result(result_file: str) -> Optional[dict]:
    if not result_file:
        return None
    path = SERVICE_REPORT_DIR / result_file
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _insert_research_task(
    *,
    task_id: str,
    task_type: str,
    start_date: str,
    end_date: str,
    top_k: Optional[int],
    min_list_days: Optional[int],
    parameter_trials: Optional[int],
    commission_bps: Optional[float],
    slippage_bps: Optional[float],
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> None:
    _ensure_research_task_tables(mysql_cfg)
    sql = """
    INSERT INTO factor_research_task (
      task_id, task_type, status, start_date, end_date, top_k, min_list_days,
      parameter_trials, commission_bps, slippage_bps
    ) VALUES (
      %s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s
    )
    """
    _ = request_ip, user_agent
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    task_id,
                    task_type,
                    start_date,
                    end_date,
                    top_k,
                    min_list_days,
                    parameter_trials,
                    commission_bps,
                    slippage_bps,
                ),
            )
        conn.commit()


def _mark_research_task_running(task_id: str, mysql_cfg: dict) -> None:
    _ensure_research_task_tables(mysql_cfg)
    sql = """
    UPDATE factor_research_task
    SET status='running', started_at=NOW(3), updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
        conn.commit()


def _mark_research_task_done(task_id: str, payload: dict, metrics: dict, mysql_cfg: dict) -> None:
    _ensure_research_task_tables(mysql_cfg)
    result_file = _save_research_task_result(task_id, payload)
    sql = """
    UPDATE factor_research_task
    SET status='done',
        row_count=%s,
        result_file=%s,
        query_ms=%s,
        total_ms=%s,
        error_message=NULL,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    int(payload.get("count") or 0),
                    result_file,
                    int(metrics.get("query_ms") or 0),
                    int(metrics.get("total_ms") or 0),
                    task_id,
                ),
            )
        conn.commit()


def _mark_research_task_error(task_id: str, error_message: str, total_ms: int, mysql_cfg: dict) -> None:
    _ensure_research_task_tables(mysql_cfg)
    sql = """
    UPDATE factor_research_task
    SET status='error',
        error_message=%s,
        total_ms=%s,
        finished_at=NOW(3),
        updated_at=NOW(3)
    WHERE task_id=%s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, ((error_message or "")[:4096], max(0, int(total_ms)), task_id))
        conn.commit()


def _fetch_research_task(task_id: str, mysql_cfg: dict) -> Optional[dict]:
    _ensure_research_task_tables(mysql_cfg)
    sql = """
    SELECT
      task_id, task_type, status, start_date, end_date, top_k, min_list_days,
      parameter_trials, commission_bps, slippage_bps, row_count, result_file,
      query_ms, total_ms, error_message, created_at, started_at, finished_at
    FROM factor_research_task
    WHERE task_id = %s
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            row = cursor.fetchone()
    return _jsonify_row(row) if row else None


def _list_research_tasks(
    *,
    task_type: str,
    page: int,
    page_size: int,
    status: str,
    mysql_cfg: dict,
) -> tuple[int, list[dict]]:
    _ensure_research_task_tables(mysql_cfg)
    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 100))
    offset = (safe_page - 1) * safe_size
    args: list[Any] = [task_type]
    where = "WHERE task_type=%s"
    if status:
        where += " AND status=%s"
        args.append(status)
    count_sql = f"SELECT COUNT(*) AS total FROM factor_research_task {where}"
    list_sql = f"""
    SELECT
      task_id, task_type, status, start_date, end_date, top_k, min_list_days,
      parameter_trials, commission_bps, slippage_bps, row_count, query_ms, total_ms,
      error_message, created_at, started_at, finished_at
    FROM factor_research_task
    {where}
    ORDER BY created_at DESC
    LIMIT %s OFFSET %s
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, tuple(args))
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(list_sql, tuple(args + [safe_size, offset]))
            rows = cursor.fetchall() or []
    return total, [_jsonify_row(r) for r in rows]


def _resolve_factor_research_range(
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    mysql_cfg: dict,
) -> tuple[str, str]:
    if not start_date and not end_date:
        return _resolve_default_range_by_trade_days(
            mysql_cfg=mysql_cfg,
            lookback_trade_days=BREADTH_FACTOR_LOOKBACK_TRADE_DAYS,
        )
    sql = "SELECT MAX(trade_date) AS max_date FROM stock_daily WHERE asset_type='E'"
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone() or {}
    max_date = row.get("max_date")
    if not max_date:
        raise HTTPException(status_code=404, detail="stock_daily has no data")

    end_dt = _parse_date_ymd(end_date, "end_date") if end_date else max_date
    if start_date:
        start_dt = _parse_date_ymd(start_date, "start_date")
    else:
        sql = """
        SELECT trade_date
        FROM (
          SELECT DISTINCT trade_date
          FROM stock_daily
          WHERE trade_date <= %s
            AND asset_type = 'E'
          ORDER BY trade_date DESC
          LIMIT %s
        ) x
        ORDER BY trade_date ASC
        LIMIT 1
        """
        with _mysql_connect(mysql_cfg) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (end_dt.isoformat(), BREADTH_FACTOR_LOOKBACK_TRADE_DAYS))
                picked = cursor.fetchone()
        start_dt = (picked or {}).get("trade_date") or end_dt
    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    return start_dt.isoformat(), end_dt.isoformat()


def _execute_factor_incremental_query(
    *,
    start_date: str,
    end_date: str,
    top_k: int,
    min_list_days: int,
    mysql_cfg: dict,
) -> tuple[dict, dict]:
    started = time.time()
    hist_start = (date.fromisoformat(start_date) - timedelta(days=90)).isoformat()
    q_started = time.time()
    sql = """
    SELECT
      d.trade_date,
      d.ts_code,
      b.name,
      b.industry,
      b.list_date,
      d.close,
      d.amount,
      d.net_mf_amount,
      d.pct_chg
    FROM stock_daily d
    JOIN stock_basic b ON b.ts_code = d.ts_code
    WHERE d.asset_type = 'E'
      AND b.asset_type = 'E'
      AND b.list_status = 'L'
      AND d.trade_date BETWEEN %s AND %s
    ORDER BY d.ts_code ASC, d.trade_date ASC
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (hist_start, end_date))
            rows = cursor.fetchall() or []
    query_ms = int((time.time() - q_started) * 1000)

    by_code: dict[str, list[dict]] = {}
    for row in rows:
        code = str(row.get("ts_code") or "")
        if not code:
            continue
        by_code.setdefault(code, []).append(row)

    factors_by_date: dict[str, list[dict]] = {}
    total_factor_rows = 0
    for code, items in by_code.items():
        closes: list[float] = []
        pcts: list[float] = []
        amts: list[float] = []
        for row in items:
            trade_date = str(row.get("trade_date") or "")
            if not trade_date or trade_date < start_date or trade_date > end_date:
                pass
            close_v = row.get("close")
            if close_v is None:
                continue
            close_f = float(close_v)
            amount_f = float(row.get("amount") or 0.0)
            pct_f = float(row.get("pct_chg") or 0.0)
            closes.append(close_f)
            pcts.append(pct_f)
            amts.append(amount_f)
            i = len(closes) - 1
            if i < 20:
                continue
            if not trade_date or trade_date < start_date or trade_date > end_date:
                continue
            list_date = row.get("list_date")
            if list_date:
                if isinstance(list_date, str):
                    list_date = date.fromisoformat(list_date)
                list_days = (date.fromisoformat(trade_date) - list_date).days
                if list_days < min_list_days:
                    continue
            prev5 = closes[i - 5]
            prev20 = closes[i - 20]
            if prev5 <= 0 or prev20 <= 0:
                continue
            last20_pct = pcts[i - 19 : i + 1]
            last20_amt = amts[i - 19 : i + 1]
            if not last20_pct or not last20_amt:
                continue
            avg_pct = sum(last20_pct) / len(last20_pct)
            avg_amt = sum(last20_amt) / len(last20_amt)
            var_pct = sum((x - avg_pct) ** 2 for x in last20_pct) / len(last20_pct)
            vol20 = var_pct ** 0.5
            mf_amt = float(row.get("net_mf_amount") or 0.0)
            mf_intensity = (mf_amt / amount_f) if amount_f > 0 else 0.0
            liq_log = math.log(max(avg_amt, 1.0))
            record = {
                "trade_date": trade_date,
                "ts_code": code,
                "name": str(row.get("name") or code),
                "industry": str(row.get("industry") or ""),
                "ret_5d": (close_f / prev5 - 1.0) * 100.0,
                "ret_20d": (close_f / prev20 - 1.0) * 100.0,
                "vol_20d": vol20,
                "mf_intensity": mf_intensity,
                "avg_amount_20d": avg_amt,
                "liq_log": liq_log,
            }
            factors_by_date.setdefault(trade_date, []).append(record)
            total_factor_rows += 1

    sampled: list[dict] = []
    daily_stats: list[dict] = []
    for td in sorted(factors_by_date.keys()):
        day_rows = factors_by_date[td]
        if not day_rows:
            continue
        def _z(values: list[float]) -> list[float]:
            avg_v = sum(values) / len(values)
            var_v = sum((x - avg_v) ** 2 for x in values) / max(1, len(values))
            std_v = var_v ** 0.5
            if std_v <= 1e-12:
                return [0.0 for _ in values]
            return [(x - avg_v) / std_v for x in values]

        z_ret20 = _z([float(x["ret_20d"]) for x in day_rows])
        z_mf = _z([float(x["mf_intensity"]) for x in day_rows])
        z_liq = _z([float(x["avg_amount_20d"]) for x in day_rows])
        z_vol = _z([float(x["vol_20d"]) for x in day_rows])
        for idx, item in enumerate(day_rows):
            score = 0.40 * z_ret20[idx] + 0.30 * z_mf[idx] + 0.20 * z_liq[idx] - 0.10 * z_vol[idx]
            item["factor_score"] = round(score, 6)
        day_rows.sort(key=lambda x: float(x["factor_score"]), reverse=True)
        picked = day_rows[: max(1, top_k)]
        for rank_no, item in enumerate(picked, start=1):
            sampled.append(
                {
                    "trade_date": item["trade_date"],
                    "rank_no": rank_no,
                    "ts_code": item["ts_code"],
                    "name": item["name"],
                    "industry": item["industry"],
                    "factor_score": round(float(item["factor_score"]), 4),
                    "ret_5d": round(float(item["ret_5d"]), 3),
                    "ret_20d": round(float(item["ret_20d"]), 3),
                    "vol_20d": round(float(item["vol_20d"]), 3),
                    "mf_intensity": round(float(item["mf_intensity"]), 5),
                    "avg_amount_20d": round(float(item["avg_amount_20d"]), 2),
                }
            )
        daily_stats.append(
            {
                "trade_date": td,
                "universe_count": len(day_rows),
                "picked_count": len(picked),
                "top_score": round(float(picked[0]["factor_score"]), 4) if picked else None,
                "median_score": round(float(day_rows[len(day_rows) // 2]["factor_score"]), 4),
            }
        )

    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": len(daily_stats),
        "factor_rows": total_factor_rows,
        "latest_trade_date": daily_stats[-1]["trade_date"] if daily_stats else None,
        "latest_universe_count": daily_stats[-1]["universe_count"] if daily_stats else 0,
        "latest_picked_count": daily_stats[-1]["picked_count"] if daily_stats else 0,
    }
    result = {
        "task_type": "factor_incremental",
        "start_date": start_date,
        "end_date": end_date,
        "count": total_factor_rows,
        "sampled_count": len(sampled),
        "sample_points": max(1, top_k),
        "items": sampled,
        "daily_stats": daily_stats,
        "summary": summary,
        "params": {
            "top_k": max(1, top_k),
            "min_list_days": max(0, min_list_days),
        },
    }
    metrics = {
        "query_ms": query_ms,
        "total_ms": int((time.time() - started) * 1000),
    }
    result["metrics"] = metrics
    return result, metrics


def _execute_backtest_guardrail_query(
    *,
    start_date: str,
    end_date: str,
    parameter_trials: int,
    commission_bps: float,
    slippage_bps: float,
    mysql_cfg: dict,
) -> tuple[dict, dict]:
    started = time.time()
    q_started = time.time()
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  COUNT(*) AS row_count,
                  COUNT(DISTINCT CONCAT(ts_code, '#', trade_date)) AS uniq_count,
                  SUM(CASE WHEN pct_chg IS NULL THEN 1 ELSE 0 END) AS pct_null_count,
                  SUM(CASE WHEN amount IS NULL OR amount <= 0 THEN 1 ELSE 0 END) AS amount_bad_count
                FROM stock_daily
                WHERE asset_type='E'
                  AND trade_date BETWEEN %s AND %s
                """,
                (start_date, end_date),
            )
            row_stats = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT
                  COUNT(*) AS trading_days,
                  AVG(total_count) AS avg_universe
                FROM market_breadth_daily
                WHERE trade_date BETWEEN %s AND %s
                """,
                (start_date, end_date),
            )
            breadth_stats = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT
                  AVG(CASE WHEN day_sum > 0 THEN day_max / day_sum ELSE 0 END) AS avg_top1_share
                FROM (
                  SELECT trade_date, SUM(amount) AS day_sum, MAX(amount) AS day_max
                  FROM stock_daily
                  WHERE asset_type='E'
                    AND trade_date BETWEEN %s AND %s
                  GROUP BY trade_date
                ) t
                """,
                (start_date, end_date),
            )
            liq_stats = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT COUNT(*) AS future_rows
                FROM stock_daily
                WHERE asset_type='E' AND trade_date > CURDATE()
                """
            )
            future_stats = cursor.fetchone() or {}

    query_ms = int((time.time() - q_started) * 1000)

    row_count = int(row_stats.get("row_count") or 0)
    uniq_count = int(row_stats.get("uniq_count") or 0)
    dup_ratio = 0.0 if row_count <= 0 else max(0.0, 1.0 - (uniq_count / max(1, row_count)))
    pct_null_ratio = 0.0 if row_count <= 0 else float(row_stats.get("pct_null_count") or 0) / row_count
    amount_bad_ratio = 0.0 if row_count <= 0 else float(row_stats.get("amount_bad_count") or 0) / row_count
    trading_days = int(breadth_stats.get("trading_days") or 0)
    avg_universe = float(breadth_stats.get("avg_universe") or 0.0)
    avg_top1_share = float(liq_stats.get("avg_top1_share") or 0.0)
    future_rows = int(future_stats.get("future_rows") or 0)
    total_cost_bps = float(commission_bps) + float(slippage_bps)

    checks = [
        {
            "check_id": "no_future_rows",
            "title": "未来日期泄漏检查",
            "status": "pass" if future_rows == 0 else "fail",
            "value": future_rows,
            "threshold": "必须=0",
            "detail": "stock_daily 中 trade_date > CURDATE() 的行数",
        },
        {
            "check_id": "pk_uniqueness",
            "title": "主键唯一性检查",
            "status": "pass" if dup_ratio == 0 else ("warn" if dup_ratio < 0.0005 else "fail"),
            "value": round(dup_ratio * 100, 4),
            "threshold": "<0.05%",
            "detail": "按(ts_code, trade_date)去重后的重复比例",
        },
        {
            "check_id": "missing_pct_chg",
            "title": "收益字段缺失率",
            "status": "pass" if pct_null_ratio <= 0.01 else ("warn" if pct_null_ratio <= 0.03 else "fail"),
            "value": round(pct_null_ratio * 100, 3),
            "threshold": "<=1% 优",
            "detail": "pct_chg 缺失会直接污染IC/分层收益",
        },
        {
            "check_id": "tradable_amount",
            "title": "可交易性检查",
            "status": "pass" if amount_bad_ratio <= 0.01 else ("warn" if amount_bad_ratio <= 0.05 else "fail"),
            "value": round(amount_bad_ratio * 100, 3),
            "threshold": "<=1% 优",
            "detail": "amount为空或<=0的比例",
        },
        {
            "check_id": "sample_days",
            "title": "样本交易日充足性",
            "status": "pass" if trading_days >= 120 else ("warn" if trading_days >= 60 else "fail"),
            "value": trading_days,
            "threshold": ">=120 日优",
            "detail": "短样本容易把噪声当信号",
        },
        {
            "check_id": "avg_universe_size",
            "title": "每日样本覆盖",
            "status": "pass" if avg_universe >= 3000 else ("warn" if avg_universe >= 2000 else "fail"),
            "value": int(round(avg_universe)),
            "threshold": ">=3000 优",
            "detail": "横截面太窄时分层统计波动会放大",
        },
        {
            "check_id": "liquidity_concentration",
            "title": "流动性集中度",
            "status": "pass" if avg_top1_share <= 0.05 else ("warn" if avg_top1_share <= 0.08 else "fail"),
            "value": round(avg_top1_share * 100, 3),
            "threshold": "<=5% 优",
            "detail": "单日最大成交额个股占全市场成交额比例均值",
        },
        {
            "check_id": "param_sweep",
            "title": "参数搜索强度",
            "status": "pass" if parameter_trials <= 20 else ("warn" if parameter_trials <= 60 else "fail"),
            "value": int(parameter_trials),
            "threshold": "<=20 优",
            "detail": "尝试参数越多，越需要更严格样本外验证",
        },
        {
            "check_id": "cost_assumption",
            "title": "交易成本假设",
            "status": "pass" if total_cost_bps >= 8 else ("warn" if total_cost_bps >= 4 else "fail"),
            "value": round(total_cost_bps, 2),
            "threshold": ">=8 bps 优",
            "detail": "默认建议至少覆盖佣金+滑点+部分冲击成本",
        },
    ]

    pass_count = sum(1 for x in checks if x["status"] == "pass")
    warn_count = sum(1 for x in checks if x["status"] == "warn")
    fail_count = sum(1 for x in checks if x["status"] == "fail")
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "checks": len(checks),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "trading_days": trading_days,
        "row_count": row_count,
    }
    result = {
        "task_type": "backtest_guardrail",
        "start_date": start_date,
        "end_date": end_date,
        "count": len(checks),
        "sampled_count": len(checks),
        "items": checks,
        "summary": summary,
        "params": {
            "parameter_trials": int(parameter_trials),
            "commission_bps": float(commission_bps),
            "slippage_bps": float(slippage_bps),
        },
    }
    metrics = {
        "query_ms": query_ms,
        "total_ms": int((time.time() - started) * 1000),
    }
    result["metrics"] = metrics
    return result, metrics


def _run_research_task(
    task_id: str,
    *,
    task_type: str,
    start_date: str,
    end_date: str,
    top_k: int,
    min_list_days: int,
    parameter_trials: int,
    commission_bps: float,
    slippage_bps: float,
    mysql_cfg: dict,
) -> None:
    started = time.time()
    _mark_research_task_running(task_id, mysql_cfg)
    try:
        if task_type == "backtest_guardrail":
            payload, metrics = _execute_backtest_guardrail_query(
                start_date=start_date,
                end_date=end_date,
                parameter_trials=parameter_trials,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
                mysql_cfg=mysql_cfg,
            )
        else:
            payload, metrics = _execute_factor_incremental_query(
                start_date=start_date,
                end_date=end_date,
                top_k=top_k,
                min_list_days=min_list_days,
                mysql_cfg=mysql_cfg,
            )
        _mark_research_task_done(task_id, payload, metrics, mysql_cfg)
    except Exception as exc:
        _mark_research_task_error(
            task_id=task_id,
            error_message=str(exc),
            total_ms=int((time.time() - started) * 1000),
            mysql_cfg=mysql_cfg,
        )


def _submit_research_task(
    *,
    task_type: str,
    start_date: str,
    end_date: str,
    top_k: int,
    min_list_days: int,
    parameter_trials: int,
    commission_bps: float,
    slippage_bps: float,
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> str:
    task_id = uuid.uuid4().hex[:16]
    _insert_research_task(
        task_id=task_id,
        task_type=task_type,
        start_date=start_date,
        end_date=end_date,
        top_k=top_k if task_type == "factor_incremental" else None,
        min_list_days=min_list_days if task_type == "factor_incremental" else None,
        parameter_trials=parameter_trials if task_type == "backtest_guardrail" else None,
        commission_bps=commission_bps if task_type == "backtest_guardrail" else None,
        slippage_bps=slippage_bps if task_type == "backtest_guardrail" else None,
        request_ip=request_ip,
        user_agent=user_agent,
        mysql_cfg=mysql_cfg,
    )
    RESEARCH_TASK_EXECUTOR.submit(
        _run_research_task,
        task_id,
        task_type=task_type,
        start_date=start_date,
        end_date=end_date,
        top_k=top_k,
        min_list_days=min_list_days,
        parameter_trials=parameter_trials,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        mysql_cfg=mysql_cfg,
    )
    return task_id


def _run_uptrend_scan_task(
    task_id: str,
    *,
    asset_type: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    mysql_cfg: dict,
) -> None:
    start_ts = time.time()
    _mark_uptrend_task_running(task_id, mysql_cfg)
    try:
        if asset_type == "F":
            result = _execute_uptrend_fund_scan(
                threshold=threshold,
                min_gap=min_gap,
                lookback_days=lookback_days,
                min_rows=min_rows,
                top_k=top_k,
                end_date=end_date,
                mysql_cfg=mysql_cfg,
            )
        else:
            result = _execute_uptrend_scan(
                threshold=threshold,
                min_gap=min_gap,
                lookback_days=lookback_days,
                min_rows=min_rows,
                top_k=top_k,
                end_date=end_date,
                mysql_cfg=mysql_cfg,
            )
        _mark_uptrend_task_done(
            task_id=task_id,
            result=result,
            duration_ms=int((time.time() - start_ts) * 1000),
            mysql_cfg=mysql_cfg,
        )
    except Exception as exc:
        _mark_uptrend_task_error(
            task_id=task_id,
            error_message=str(exc),
            duration_ms=int((time.time() - start_ts) * 1000),
            mysql_cfg=mysql_cfg,
        )


def _submit_uptrend_task(
    *,
    asset_type: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> str:
    task_id = uuid.uuid4().hex[:16]
    _insert_uptrend_task(
        task_id=task_id,
        asset_type=asset_type,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        min_rows=min_rows,
        top_k=top_k,
        end_date=end_date,
        request_ip=request_ip,
        user_agent=user_agent,
        mysql_cfg=mysql_cfg,
    )

    UPTREND_TASK_EXECUTOR.submit(
        _run_uptrend_scan_task,
        task_id,
        asset_type=asset_type,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        min_rows=min_rows,
        top_k=top_k,
        end_date=end_date,
        mysql_cfg=mysql_cfg,
    )
    return task_id


def _execute_uptrend_scan(
    *,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    mysql_cfg: dict,
) -> dict:
    rows = find_uptrend_stocks(
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        min_rows=min_rows,
        end_date=date.fromisoformat(end_date) if end_date else None,
        max_stocks=0,
        mysql_config=mysql_cfg,
    )
    limited = rows if top_k <= 0 else rows[:top_k]
    return {
        "count": len(rows),
        "returned": len(limited),
        "items": [asdict(x) for x in limited],
        "params": {
            "threshold": threshold,
            "min_gap": min_gap,
            "lookback_days": lookback_days,
            "min_rows": min_rows,
            "top_k": top_k,
            "end_date": end_date,
        },
    }


def _execute_uptrend_fund_scan(
    *,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    mysql_cfg: dict,
) -> dict:
    rows = find_uptrend_funds(
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        min_rows=min_rows,
        end_date=date.fromisoformat(end_date) if end_date else None,
        max_funds=0,
        mysql_config=mysql_cfg,
    )
    limited = rows if top_k <= 0 else rows[:top_k]
    return {
        "count": len(rows),
        "returned": len(limited),
        "items": [asdict(x) for x in limited],
        "params": {
            "threshold": threshold,
            "min_gap": min_gap,
            "lookback_days": lookback_days,
            "min_rows": min_rows,
            "top_k": top_k,
            "end_date": end_date,
        },
    }


def _execute_market_breadth_query(
    *,
    start_date: str,
    end_date: str,
    with_report: bool,
    sample_points: int,
    mysql_cfg: dict,
) -> tuple[dict, dict]:
    started = time.time()
    cache_key = _breadth_cache_key(
        start_date=start_date,
        end_date=end_date,
        with_report=with_report,
        sample_points=sample_points,
        mysql_cfg=mysql_cfg,
    )
    cached = _breadth_cache_get(cache_key)
    if cached:
        metrics = dict(cached.get("metrics") or {})
        metrics.update({"cache_hit": True, "total_ms": int((time.time() - started) * 1000)})
        out = dict(cached)
        out["metrics"] = metrics
        return out, metrics

    q_started = time.time()
    rows = _fetch_market_breadth_rows(start_date=start_date, end_date=end_date, mysql_cfg=mysql_cfg)
    query_ms = int((time.time() - q_started) * 1000)
    summary = _build_market_breadth_summary(rows, start_date, end_date)
    sampled = _downsample_rows(rows, max(1, sample_points))
    result = {
        "start_date": start_date,
        "end_date": end_date,
        "count": len(rows),
        "sampled_count": len(sampled),
        "sample_points": max(1, sample_points),
        "items": sampled,
        "summary": summary,
    }

    render_ms = 0
    if with_report:
        r_started = time.time()
        report_id = uuid.uuid4().hex[:12]
        html = _make_market_breadth_html(sampled, summary)
        metadata = {
            "report_id": report_id,
            "report_type": "market_breadth_daily",
            "html_url": f"/api/reports/{report_id}/html",
            "summary": summary,
            "created_from": {
                "start_date": start_date,
                "end_date": end_date,
                "count": len(rows),
                "sampled_count": len(sampled),
                "sample_points": max(1, sample_points),
            },
        }
        _save_report(report_id, html, metadata)
        render_ms = int((time.time() - r_started) * 1000)
        result["report_id"] = report_id
        result["html_url"] = metadata["html_url"]

    metrics = {
        "cache_hit": False,
        "query_ms": query_ms,
        "render_ms": render_ms,
        "total_ms": int((time.time() - started) * 1000),
    }
    result["metrics"] = metrics
    _breadth_cache_set(cache_key, result)
    return result, metrics


def _run_breadth_task(
    task_id: str,
    *,
    start_date: str,
    end_date: str,
    with_report: bool,
    sample_points: int,
    mysql_cfg: dict,
) -> None:
    started = time.time()
    _mark_breadth_task_running(task_id, mysql_cfg)
    try:
        payload, metrics = _execute_market_breadth_query(
            start_date=start_date,
            end_date=end_date,
            with_report=with_report,
            sample_points=sample_points,
            mysql_cfg=mysql_cfg,
        )
        _mark_breadth_task_done(task_id, payload, metrics, mysql_cfg)
    except Exception as exc:
        _mark_breadth_task_error(
            task_id=task_id,
            error_message=str(exc),
            total_ms=int((time.time() - started) * 1000),
            mysql_cfg=mysql_cfg,
        )


def _submit_breadth_task(
    *,
    start_date: str,
    end_date: str,
    with_report: bool,
    sample_points: int,
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> str:
    task_id = uuid.uuid4().hex[:16]
    _insert_breadth_task(
        task_id=task_id,
        start_date=start_date,
        end_date=end_date,
        with_report=with_report,
        sample_points=sample_points,
        request_ip=request_ip,
        user_agent=user_agent,
        mysql_cfg=mysql_cfg,
    )
    BREADTH_TASK_EXECUTOR.submit(
        _run_breadth_task,
        task_id,
        start_date=start_date,
        end_date=end_date,
        with_report=with_report,
        sample_points=sample_points,
        mysql_cfg=mysql_cfg,
    )
    return task_id


def _parse_date_ymd(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field_name}, expected YYYY-MM-DD") from exc


def _downsample_rows(rows: list[dict], max_points: int) -> list[dict]:
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    if max_points == 1:
        return [rows[-1]]
    step = (len(rows) - 1) / float(max_points - 1)
    out = []
    used = set()
    for i in range(max_points):
        idx = int(round(i * step))
        idx = min(len(rows) - 1, max(0, idx))
        if idx in used:
            continue
        used.add(idx)
        out.append(rows[idx])
    if out and out[-1] != rows[-1]:
        out[-1] = rows[-1]
    return out


def _breadth_cache_key(
    *,
    start_date: str,
    end_date: str,
    with_report: bool,
    sample_points: int,
    mysql_cfg: dict,
) -> str:
    return "|".join(
        [
            str(mysql_cfg.get("host", "")),
            str(mysql_cfg.get("port", "")),
            str(mysql_cfg.get("database", "")),
            start_date,
            end_date,
            "1" if with_report else "0",
            str(sample_points),
        ]
    )


def _breadth_cache_get(key: str) -> Optional[dict]:
    now = time.time()
    with BREADTH_CACHE_LOCK:
        item = BREADTH_RESULT_CACHE.get(key)
        if not item:
            return None
        expire_at, payload = item
        if now > expire_at:
            del BREADTH_RESULT_CACHE[key]
            return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    html_url = data.get("html_url")
    if html_url and "/api/reports/" in html_url:
        report_id = str(html_url).split("/api/reports/")[-1].split("/")[0]
        html_path = SERVICE_REPORT_DIR / f"{report_id}.html"
        if not html_path.exists():
            return None
    return data


def _breadth_cache_set(key: str, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    expire_at = time.time() + BREADTH_CACHE_TTL_SEC
    with BREADTH_CACHE_LOCK:
        BREADTH_RESULT_CACHE[key] = (expire_at, payload)


def _breadth_cache_delete(key: str) -> None:
    with BREADTH_CACHE_LOCK:
        if key in BREADTH_RESULT_CACHE:
            del BREADTH_RESULT_CACHE[key]


def _resolve_default_range_by_trade_days(
    *,
    mysql_cfg: dict,
    lookback_trade_days: int,
) -> tuple[str, str]:
    sql = """
    SELECT trade_date
    FROM (
      SELECT DISTINCT trade_date
      FROM stock_daily
      WHERE asset_type = 'E'
      ORDER BY trade_date DESC
      LIMIT %s
    ) x
    ORDER BY trade_date ASC
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (max(2, lookback_trade_days),))
            rows = cursor.fetchall() or []
    if not rows:
        raise HTTPException(status_code=404, detail="stock_daily has no data")
    return str(rows[0]["trade_date"]), str(rows[-1]["trade_date"])


def _resolve_market_breadth_range(
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    mysql_cfg: dict,
) -> tuple[str, str]:
    if not start_date and not end_date:
        return _resolve_default_range_by_trade_days(
            mysql_cfg=mysql_cfg,
            lookback_trade_days=BREADTH_DEFAULT_LOOKBACK_TRADE_DAYS,
        )

    sql = "SELECT MAX(trade_date) AS max_date FROM stock_daily WHERE asset_type = 'E'"
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone() or {}

    max_date = row.get("max_date")
    if not max_date:
        raise HTTPException(status_code=404, detail="stock_daily has no data")

    end_dt = _parse_date_ymd(end_date, "end_date") if end_date else max_date
    if start_date:
        start_dt = _parse_date_ymd(start_date, "start_date")
    else:
        sql = """
        SELECT trade_date
        FROM (
          SELECT DISTINCT trade_date
          FROM stock_daily
          WHERE trade_date <= %s
            AND asset_type = 'E'
          ORDER BY trade_date DESC
          LIMIT %s
        ) x
        ORDER BY trade_date ASC
        LIMIT 1
        """
        with _mysql_connect(mysql_cfg) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (end_dt.isoformat(), BREADTH_DEFAULT_LOOKBACK_TRADE_DAYS))
                picked = cursor.fetchone()
        start_dt = (picked or {}).get("trade_date") or end_dt
    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    return start_dt.isoformat(), end_dt.isoformat()


def _fetch_market_breadth_rows(
    *,
    start_date: str,
    end_date: str,
    mysql_cfg: dict,
) -> list[dict]:
    _sync_market_breadth_daily(mysql_cfg, start_date, end_date)
    sql = """
    SELECT
      trade_date,
      total_count,
      up_count,
      down_count,
      flat_count,
      up_ratio_pct,
      avg_pct_chg
    FROM market_breadth_daily
    WHERE trade_date BETWEEN %s AND %s
    ORDER BY trade_date ASC
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (start_date, end_date))
            rows = cursor.fetchall() or []
    return [_jsonify_row(r) for r in rows]


def _save_breadth_task_result(task_id: str, payload: dict) -> str:
    name = f"breadth_task_{task_id}.json"
    path = SERVICE_REPORT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return name


def _load_breadth_task_result(result_file: str) -> Optional[dict]:
    if not result_file:
        return None
    path = SERVICE_REPORT_DIR / result_file
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_market_breadth_summary(rows: list[dict], start_date: str, end_date: str) -> dict:
    if not rows:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": 0,
        }

    def _as_int(v: Any) -> int:
        return int(v or 0)

    latest = rows[-1]
    bullish_days = sum(1 for x in rows if _as_int(x.get("up_count")) > _as_int(x.get("down_count")))
    bearish_days = sum(1 for x in rows if _as_int(x.get("up_count")) < _as_int(x.get("down_count")))
    max_up_day = max(rows, key=lambda x: _as_int(x.get("up_count")))
    max_down_day = max(rows, key=lambda x: _as_int(x.get("down_count")))

    return {
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": len(rows),
        "latest_trade_date": str(latest.get("trade_date")),
        "latest_up_count": _as_int(latest.get("up_count")),
        "latest_down_count": _as_int(latest.get("down_count")),
        "latest_flat_count": _as_int(latest.get("flat_count")),
        "latest_total_count": _as_int(latest.get("total_count")),
        "latest_up_ratio_pct": float(latest.get("up_ratio_pct") or 0),
        "avg_daily_up_ratio_pct": round(
            sum(float(x.get("up_ratio_pct") or 0) for x in rows) / max(1, len(rows)),
            2,
        ),
        "bullish_days": bullish_days,
        "bearish_days": bearish_days,
        "max_up_day": {
            "trade_date": str(max_up_day.get("trade_date")),
            "up_count": _as_int(max_up_day.get("up_count")),
        },
        "max_down_day": {
            "trade_date": str(max_down_day.get("trade_date")),
            "down_count": _as_int(max_down_day.get("down_count")),
        },
    }


def _make_market_breadth_html(rows: list[dict], summary: dict) -> str:
    def _fmt_int(v: Any) -> str:
        return f"{int(v or 0):,}"

    def _fmt_pct(v: Any) -> str:
        n = float(v or 0)
        return f"{n:.2f}%"

    chart_rows = []
    row_html = []
    for item in rows:
        trade_date = html_escape(str(item.get("trade_date") or ""))
        up = int(item.get("up_count") or 0)
        down = int(item.get("down_count") or 0)
        flat = int(item.get("flat_count") or 0)
        total = int(item.get("total_count") or 0)
        up_ratio = float(item.get("up_ratio_pct") or 0)
        avg_pct = float(item.get("avg_pct_chg") or 0)
        cls = "rise" if up > down else ("fall" if up < down else "flat")
        row_html.append(
            "<tr>"
            f"<td>{trade_date}</td>"
            f"<td class='rise'>{up}</td>"
            f"<td class='fall'>{down}</td>"
            f"<td>{flat}</td>"
            f"<td>{total}</td>"
            f"<td class='{cls}'>{up_ratio:.2f}%</td>"
            f"<td class='{('rise' if avg_pct >= 0 else 'fall')}'>{avg_pct:.2f}%</td>"
            "</tr>"
        )
        chart_rows.append(
            {
                "trade_date": str(item.get("trade_date") or ""),
                "up_count": up,
                "down_count": down,
                "flat_count": flat,
                "total_count": total,
                "up_ratio_pct": round(up_ratio, 2),
                "avg_pct_chg": round(avg_pct, 4),
            }
        )

    title = f"A股每日涨跌统计（{summary.get('start_date')} ~ {summary.get('end_date')}）"
    chart_json = json.dumps(chart_rows, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html_escape(title)}</title>
  <style>
    body {{
      margin: 0;
      padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #f8fafc;
      color: #0f172a;
    }}
    h1 {{ margin: 0 0 12px; font-size: 22px; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }}
    .kpi {{
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 8px;
      background: #fff;
    }}
    .kpi .k {{ font-size: 12px; color: #64748b; }}
    .kpi .v {{ font-size: 16px; font-weight: 700; margin-top: 4px; }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }}
    .chart-card {{
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      background: #fff;
      padding: 10px;
    }}
    .chart-title {{
      margin: 0 0 8px;
      font-size: 13px;
      color: #334155;
      font-weight: 600;
    }}
    .chart-holder {{
      width: 100%;
      height: 300px;
      position: relative;
    }}
    canvas {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    .legend {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 8px;
      color: #475569;
      font-size: 12px;
    }}
    .legend i {{
      width: 10px;
      height: 10px;
      border-radius: 2px;
      display: inline-block;
      margin-right: 5px;
      vertical-align: middle;
    }}
    .table-wrap {{
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      overflow: auto;
      background: #fff;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid #f1f5f9;
      padding: 8px;
      white-space: nowrap;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f8fafc;
      z-index: 1;
    }}
    .rise {{ color: #b91c1c; }}
    .fall {{ color: #047857; }}
    .flat {{ color: #334155; }}
    .muted {{ color: #64748b; font-size: 12px; margin-top: 8px; }}
    @media (max-width: 900px) {{
      .chart-grid {{ grid-template-columns: 1fr; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <h1>{html_escape(title)}</h1>
  <div class="summary">
    <div class="kpi"><div class="k">交易日数</div><div class="v">{_fmt_int(summary.get("trading_days"))}</div></div>
    <div class="kpi"><div class="k">最新上涨/下跌</div><div class="v">{_fmt_int(summary.get("latest_up_count"))}/{_fmt_int(summary.get("latest_down_count"))}</div></div>
    <div class="kpi"><div class="k">最新上涨占比</div><div class="v">{_fmt_pct(summary.get("latest_up_ratio_pct"))}</div></div>
    <div class="kpi"><div class="k">区间平均上涨占比</div><div class="v">{_fmt_pct(summary.get("avg_daily_up_ratio_pct"))}</div></div>
    <div class="kpi"><div class="k">上涨家数最多</div><div class="v">{html_escape(str((summary.get("max_up_day") or {}).get("trade_date") or "-"))} / {_fmt_int((summary.get("max_up_day") or {}).get("up_count"))}</div></div>
    <div class="kpi"><div class="k">下跌家数最多</div><div class="v">{html_escape(str((summary.get("max_down_day") or {}).get("trade_date") or "-"))} / {_fmt_int((summary.get("max_down_day") or {}).get("down_count"))}</div></div>
    <div class="kpi"><div class="k">多头日</div><div class="v">{_fmt_int(summary.get("bullish_days"))}</div></div>
    <div class="kpi"><div class="k">空头日</div><div class="v">{_fmt_int(summary.get("bearish_days"))}</div></div>
  </div>
  <div class="chart-grid">
    <div class="chart-card">
      <div class="chart-title">市场广度结构（上涨/下跌/平盘家数）</div>
      <div class="chart-holder"><canvas id="breadthMixChart"></canvas></div>
      <div class="legend">
        <span><i style="background:#ef4444"></i>上涨家数</span>
        <span><i style="background:#10b981"></i>下跌家数</span>
        <span><i style="background:#94a3b8"></i>平盘家数</span>
      </div>
    </div>
    <div class="chart-card">
      <div class="chart-title">强弱指标（上涨占比 + 平均涨跌幅）</div>
      <div class="chart-holder"><canvas id="strengthChart"></canvas></div>
      <div class="legend">
        <span><i style="background:#dc2626"></i>上涨占比(%)</span>
        <span><i style="background:#2563eb"></i>平均涨跌幅(%)</span>
      </div>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>交易日</th>
          <th>上涨家数</th>
          <th>下跌家数</th>
          <th>平盘家数</th>
          <th>样本总数</th>
          <th>上涨占比</th>
          <th>平均涨跌幅</th>
        </tr>
      </thead>
      <tbody>
        {''.join(row_html)}
      </tbody>
    </table>
  </div>
  <div class="muted">说明：统计基于 stock_daily 的 pct_chg 字段按交易日聚合。</div>
  <script>
    const chartData = {chart_json};

    function fitCanvas(canvas) {{
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(320, canvas.clientWidth);
      const h = Math.max(220, canvas.clientHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return {{ ctx, w, h }};
    }}

    function drawAxis(ctx, left, top, innerW, innerH, yTicks, yFmt) {{
      ctx.strokeStyle = "#cbd5e1";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(left, top);
      ctx.lineTo(left, top + innerH);
      ctx.lineTo(left + innerW, top + innerH);
      ctx.stroke();
      ctx.fillStyle = "#64748b";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      yTicks.forEach((t) => {{
        const y = top + innerH * (1 - t.pos);
        ctx.strokeStyle = "#eef2ff";
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(left + innerW, y);
        ctx.stroke();
        ctx.fillText(yFmt(t.value), left - 6, y);
      }});
    }}

    function drawXLabels(ctx, left, top, innerW, innerH, labels) {{
      if (!labels.length) return;
      const step = innerW / Math.max(1, labels.length);
      const every = Math.max(1, Math.ceil(labels.length / 8));
      ctx.fillStyle = "#64748b";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      labels.forEach((label, i) => {{
        if (i % every !== 0 && i !== labels.length - 1) return;
        const x = left + step * i + step * 0.5;
        ctx.fillText(label.slice(5), x, top + innerH + 6);
      }});
    }}

    function drawBreadthMixChart() {{
      const canvas = document.getElementById("breadthMixChart");
      if (!canvas) return;
      const {{ ctx, w, h }} = fitCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      if (!chartData.length) return;

      const pad = {{ left: 46, right: 12, top: 10, bottom: 34 }};
      const innerW = w - pad.left - pad.right;
      const innerH = h - pad.top - pad.bottom;
      const maxTotal = Math.max(1, ...chartData.map((x) => Number(x.total_count || 0)));
      const yTicks = [0, 0.25, 0.5, 0.75, 1].map((pos) => ({{ pos, value: Math.round(maxTotal * pos) }}));
      drawAxis(ctx, pad.left, pad.top, innerW, innerH, yTicks, (v) => String(v));

      const step = innerW / Math.max(1, chartData.length);
      const bw = Math.max(4, Math.min(24, step * 0.62));
      chartData.forEach((item, i) => {{
        const x = pad.left + i * step + (step - bw) / 2;
        const total = Math.max(1, Number(item.total_count || 0));
        const upH = innerH * (Number(item.up_count || 0) / maxTotal);
        const downH = innerH * (Number(item.down_count || 0) / maxTotal);
        const flatH = innerH * (Number(item.flat_count || 0) / maxTotal);
        let y = pad.top + innerH;
        ctx.fillStyle = "#10b981";
        y -= downH;
        ctx.fillRect(x, y, bw, downH);
        ctx.fillStyle = "#94a3b8";
        y -= flatH;
        ctx.fillRect(x, y, bw, flatH);
        ctx.fillStyle = "#ef4444";
        y -= upH;
        ctx.fillRect(x, y, bw, upH);
        if (total <= 0) {{
          ctx.fillStyle = "#e2e8f0";
          ctx.fillRect(x, pad.top + innerH - 1, bw, 1);
        }}
      }});
      drawXLabels(ctx, pad.left, pad.top, innerW, innerH, chartData.map((x) => String(x.trade_date || "")));
    }}

    function drawLine(ctx, points, color) {{
      if (points.length < 2) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
      ctx.stroke();
      points.forEach((p) => {{
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }});
    }}

    function drawStrengthChart() {{
      const canvas = document.getElementById("strengthChart");
      if (!canvas) return;
      const {{ ctx, w, h }} = fitCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      if (!chartData.length) return;

      const pad = {{ left: 42, right: 12, top: 10, bottom: 34 }};
      const innerW = w - pad.left - pad.right;
      const innerH = h - pad.top - pad.bottom;
      const step = innerW / Math.max(1, chartData.length - 1);

      const ratios = chartData.map((x) => Number(x.up_ratio_pct || 0));
      const avgPcts = chartData.map((x) => Number(x.avg_pct_chg || 0));
      const minAvg = Math.min(...avgPcts);
      const maxAvg = Math.max(...avgPcts);
      const avgSpan = Math.max(0.5, maxAvg - minAvg);
      const avgLow = minAvg - avgSpan * 0.12;
      const avgHigh = maxAvg + avgSpan * 0.12;

      drawAxis(
        ctx,
        pad.left,
        pad.top,
        innerW,
        innerH,
        [0, 0.25, 0.5, 0.75, 1].map((pos) => ({{ pos, value: Math.round(100 * pos) }})),
        (v) => `${{v}}%`
      );

      const ratioPts = ratios.map((v, i) => {{
        const x = pad.left + i * step;
        const y = pad.top + innerH * (1 - Math.max(0, Math.min(100, v)) / 100);
        return {{ x, y }};
      }});

      const avgPts = avgPcts.map((v, i) => {{
        const x = pad.left + i * step;
        const y = pad.top + innerH * (1 - ((v - avgLow) / Math.max(0.001, avgHigh - avgLow)));
        return {{ x, y }};
      }});

      drawLine(ctx, ratioPts, "#dc2626");
      drawLine(ctx, avgPts, "#2563eb");

      drawXLabels(ctx, pad.left, pad.top, innerW, innerH, chartData.map((x) => String(x.trade_date || "")));

      ctx.fillStyle = "#2563eb";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(`${{avgHigh.toFixed(2)}}%`, pad.left - 6, pad.top + 8);
      ctx.fillText(`${{avgLow.toFixed(2)}}%`, pad.left - 6, pad.top + innerH - 8);
    }}

    function redrawCharts() {{
      drawBreadthMixChart();
      drawStrengthChart();
    }}

    window.addEventListener("resize", () => {{
      clearTimeout(window.__breadthResizeTimer);
      window.__breadthResizeTimer = setTimeout(redrawCharts, 120);
    }});
    redrawCharts();
  </script>
</body>
</html>"""


app = FastAPI(title="Cycle Report Service", version="0.1.0")
app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


class GenerateRequest(BaseModel):
    name: str = Field(..., description="Stock name, e.g. 中际旭创")
    threshold: float = Field(0.08, ge=0.005, le=0.8, description="ZigZag threshold")
    min_gap: int = Field(5, ge=1, le=60, description="Minimum trade-day gap between pivots")
    lookback_days: int = Field(365, ge=60)
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default latest trade date")
    mysql_host: str = DEFAULT_MYSQL_HOST
    mysql_port: int = DEFAULT_MYSQL_PORT
    mysql_user: str = DEFAULT_MYSQL_USER
    mysql_database: str = DEFAULT_MYSQL_DB
    mysql_password: Optional[str] = Field(
        None, description="If omitted, uses MYSQL_PASSWORD env var"
    )
    asset_type: str = Field("E", description="E=stock, H=hk stock, F=fund")
    output_format: str = Field("html", description="html|json|both")
    template_version: str = Field(DEFAULT_TEMPLATE_VERSION, description="report template version")
    async_mode: bool = Field(True, description="run in async task mode")


class GenerateByCodeRequest(BaseModel):
    ts_code: str = Field(..., description="Stock ts_code, e.g. 300308.SZ")
    threshold: float = Field(0.08, ge=0.005, le=0.8, description="ZigZag threshold")
    min_gap: int = Field(5, ge=1, le=60, description="Minimum trade-day gap between pivots")
    lookback_days: int = Field(365, ge=60)
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default latest trade date")
    mysql_host: str = DEFAULT_MYSQL_HOST
    mysql_port: int = DEFAULT_MYSQL_PORT
    mysql_user: str = DEFAULT_MYSQL_USER
    mysql_database: str = DEFAULT_MYSQL_DB
    mysql_password: Optional[str] = Field(
        None, description="If omitted, uses MYSQL_PASSWORD env var"
    )
    asset_type: str = Field("E", description="E=stock, H=hk stock, F=fund")
    output_format: str = Field("html", description="html|json|both")
    template_version: str = Field(DEFAULT_TEMPLATE_VERSION, description="report template version")
    async_mode: bool = Field(True, description="run in async task mode")


class UptrendQuery(BaseModel):
    threshold: float = Field(0.08, ge=0.005, le=0.8)
    min_gap: int = Field(5, ge=1, le=60)
    lookback_days: int = Field(365, ge=60)
    min_rows: int = Field(60, ge=20, le=2000)
    top_k: int = Field(0, ge=0, le=20000, description="0 means all")
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default today")


class EventCreateRequest(BaseModel):
    trade_date: str = Field(..., description="YYYY-MM-DD")
    title: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=4000)
    ts_codes: list[str] = Field(default_factory=list)
    global_event: bool = Field(False, description="True means visible to all assets")
    tags: Optional[str] = Field(None, max_length=255)
    source: str = Field("manual", max_length=32)


def _save_report(report_id: str, html: str, metadata: dict) -> Path:
    html_path = SERVICE_REPORT_DIR / f"{report_id}.html"
    meta_path = SERVICE_REPORT_DIR / f"{report_id}.json"
    html_path.write_text(html, encoding="utf-8")
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path


def _load_metadata(report_id: str) -> dict:
    meta_path = SERVICE_REPORT_DIR / f"{report_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="report not found")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _find_asset_name_by_code(ts_code: str, mysql_cfg: dict, asset_type: str) -> str:
    safe_type = (asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H"}:
        raise HTTPException(status_code=400, detail="asset_type must be E/F/H")
    sql = """
    SELECT name
    FROM stock_basic
    WHERE ts_code = %s
      AND asset_type = %s
      AND list_status = 'L'
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (ts_code, safe_type))
            row = cursor.fetchone() or {}
    name = str(row.get("name") or "").strip()
    if not name:
        kind = "stock" if safe_type == "E" else "fund"
        raise HTTPException(status_code=404, detail=f"{kind} not found by ts_code: {ts_code}")
    return name


def _latest_trade_date_by_asset(mysql_cfg: dict, asset_type: str) -> str:
    safe_type = (asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H"}:
        raise HTTPException(status_code=400, detail="asset_type must be E/F/H")
    sql = "SELECT MAX(trade_date) AS max_trade_date FROM stock_daily WHERE asset_type = %s"
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (safe_type,))
            row = cursor.fetchone() or {}
    max_trade_date = row.get("max_trade_date")
    if not max_trade_date:
        kind = "stock" if safe_type == "E" else "fund"
        raise HTTPException(status_code=404, detail=f"no trade_date found for {kind}")
    return str(max_trade_date)


def _resolve_asset_by_name(
    *,
    name: str,
    mysql_cfg: dict,
    asset_type: str,
) -> tuple[str, str]:
    safe_type = (asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H"}:
        raise HTTPException(status_code=400, detail="asset_type must be E/F/H")
    query = (name or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="name is required")

    variants = build_query_variants(query)
    args: list[Any] = [safe_type]
    where = ""
    if variants:
        clauses = []
        for token in variants:
            clauses.append("(name = %s OR symbol = %s OR ts_code = %s)")
            args.extend([token, token, token.upper()])
        where = " AND (" + " OR ".join(clauses) + ")"

    exact_sql = f"""
    SELECT ts_code, symbol, name
    FROM stock_basic
    WHERE asset_type = %s
      AND list_status = 'L'
      {where}
    ORDER BY ts_code
    LIMIT 1
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(exact_sql, tuple(args))
            row = cursor.fetchone()
            if not row:
                like_sql = """
                SELECT ts_code, symbol, name
                FROM stock_basic
                WHERE asset_type = %s
                  AND list_status = 'L'
                  AND (name LIKE %s OR symbol LIKE %s OR ts_code LIKE %s)
                ORDER BY ts_code
                LIMIT 1
                """
                like = f"%{query}%"
                cursor.execute(like_sql, (safe_type, like, like, like.upper()))
                row = cursor.fetchone()

    if not row:
        kind = "stock" if safe_type == "E" else "fund"
        raise HTTPException(status_code=404, detail=f"{kind} not found by name: {query}")
    return str(row.get("ts_code") or ""), str(row.get("name") or "")


def _fetch_daily_by_asset_type(
    *,
    ts_code: str,
    start_date: str,
    end_date: str,
    asset_type: str,
    mysql_cfg: dict,
) -> list[dict]:
    safe_type = (asset_type or "E").strip().upper()
    sql = """
    SELECT
      trade_date, open, high, low, close, pre_close, `change`, pct_chg, vol, amount,
      buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount,
      buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount,
      net_mf_amount
    FROM stock_daily
    WHERE ts_code = %s
      AND asset_type = %s
      AND trade_date BETWEEN %s AND %s
    ORDER BY trade_date ASC
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (ts_code, safe_type, start_date, end_date))
            rows = cursor.fetchall() or []

    out: list[dict] = []
    for row in rows:
        close_v = row.get("close")
        if close_v is None:
            continue
        close_f = float(close_v)
        open_f = float(row.get("open")) if row.get("open") is not None else close_f
        high_f = float(row.get("high")) if row.get("high") is not None else max(open_f, close_f)
        low_f = float(row.get("low")) if row.get("low") is not None else min(open_f, close_f)
        pre_close = row.get("pre_close")
        pre_close_f = float(pre_close) if pre_close is not None else close_f
        out.append(
            {
                "trade_date": str(row.get("trade_date") or ""),
                "open": open_f,
                "high": high_f,
                "low": low_f,
                "close": close_f,
                "pre_close": pre_close_f,
                "change": float(row.get("change")) if row.get("change") is not None else None,
                "pct_chg": float(row.get("pct_chg")) if row.get("pct_chg") is not None else None,
                "vol": float(row.get("vol")) if row.get("vol") is not None else None,
                "amount": float(row.get("amount")) if row.get("amount") is not None else None,
                "buy_sm_amount": float(row.get("buy_sm_amount")) if row.get("buy_sm_amount") is not None else None,
                "sell_sm_amount": float(row.get("sell_sm_amount")) if row.get("sell_sm_amount") is not None else None,
                "buy_md_amount": float(row.get("buy_md_amount")) if row.get("buy_md_amount") is not None else None,
                "sell_md_amount": float(row.get("sell_md_amount")) if row.get("sell_md_amount") is not None else None,
                "buy_lg_amount": float(row.get("buy_lg_amount")) if row.get("buy_lg_amount") is not None else None,
                "sell_lg_amount": float(row.get("sell_lg_amount")) if row.get("sell_lg_amount") is not None else None,
                "buy_elg_amount": float(row.get("buy_elg_amount")) if row.get("buy_elg_amount") is not None else None,
                "sell_elg_amount": float(row.get("sell_elg_amount")) if row.get("sell_elg_amount") is not None else None,
                "net_mf_amount": float(row.get("net_mf_amount")) if row.get("net_mf_amount") is not None else None,
            }
        )
    return out


def _generate_report_impl(
    *,
    ts_code: str,
    stock_name: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    mysql_cfg: dict,
    created_from: dict,
    asset_type: str = "E",
    output_format: str = "html",
    template_version: str = DEFAULT_TEMPLATE_VERSION,
):
    safe_type = (asset_type or "E").strip().upper()
    fmt = (output_format or "html").strip().lower()
    if fmt not in {"html", "json", "both"}:
        raise HTTPException(status_code=400, detail="output_format must be html/json/both")
    end_trade_date = end_date or _latest_trade_date_by_asset(mysql_cfg, safe_type)
    start_date = (date.fromisoformat(end_trade_date) - timedelta(days=lookback_days)).isoformat()
    rows = _fetch_daily_by_asset_type(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_trade_date,
        asset_type=safe_type,
        mysql_cfg=mysql_cfg,
    )
    if len(rows) < 20:
        raise HTTPException(status_code=422, detail="not enough data in requested range")
    pivots = zigzag_pivots(rows, threshold, min_gap)
    if safe_type == "F":
        asset_label = "基金"
    elif safe_type == "H":
        asset_label = "港股"
    else:
        asset_label = "A股"
    report_data = build_cycle_payload(
        stock_name=stock_name,
        ts_code=ts_code,
        rows=rows,
        pivots=pivots,
        threshold=threshold,
        min_gap=min_gap,
        asset_type=safe_type,
        asset_label=asset_label,
    )
    report_data["events"] = _list_cycle_events(
        ts_code=ts_code,
        asset_type=safe_type,
        start_date=start_date,
        end_date=end_trade_date,
        mysql_cfg=mysql_cfg,
    )
    summary = report_data.get("summary") or {}

    report_id = uuid.uuid4().hex[:12]
    metadata = {
        "report_id": report_id,
        "summary": summary,
        "json_url": f"/api/reports/{report_id}/data",
        "created_from": created_from,
        "asset_type": safe_type,
        "template_version": template_version,
        "format": fmt,
    }
    if fmt in {"html", "both"}:
        html = render_cycle_report_html(
            report_data=report_data,
            template_version=template_version,
        )
        metadata["html_url"] = f"/api/reports/{report_id}/html"
        _save_report(report_id, html, metadata)
    else:
        meta_path = SERVICE_REPORT_DIR / f"{report_id}.json"
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    data_path = SERVICE_REPORT_DIR / f"{report_id}.data.json"
    data_path.write_text(json.dumps(report_data, ensure_ascii=False), encoding="utf-8")
    return metadata


def _generate_report_cached(
    *,
    ts_code: str,
    stock_name: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    mysql_cfg: dict,
    created_from: dict,
    asset_type: str,
    output_format: str,
    template_version: str,
) -> dict:
    cache_key = _report_cache_key(
        mysql_cfg=mysql_cfg,
        asset_type=asset_type,
        ts_code=ts_code,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        end_date=end_date,
        output_format=output_format,
        template_version=template_version,
    )
    cached = _report_cache_get(cache_key)
    if cached:
        payload = dict(cached)
        payload["cache_hit"] = True
        return payload

    payload = _generate_report_impl(
        ts_code=ts_code,
        stock_name=stock_name,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        end_date=end_date,
        mysql_cfg=mysql_cfg,
        created_from=created_from,
        asset_type=asset_type,
        output_format=output_format,
        template_version=template_version,
    )
    payload["cache_hit"] = False
    _report_cache_set(cache_key, payload)
    return payload


def _run_report_task(
    task_id: str,
    *,
    ts_code: Optional[str],
    query_name: Optional[str],
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    mysql_cfg: dict,
    asset_type: str,
    output_format: str,
    template_version: str,
) -> None:
    started = time.time()
    _mark_report_task_running(task_id, mysql_cfg)
    try:
        if ts_code:
            resolved_ts = ts_code.strip().upper()
            stock_name = _find_asset_name_by_code(resolved_ts, mysql_cfg, asset_type)
        else:
            resolved_ts, stock_name = _resolve_asset_by_name(
                name=query_name or "",
                mysql_cfg=mysql_cfg,
                asset_type=asset_type,
            )
        result = _generate_report_cached(
            ts_code=resolved_ts,
            stock_name=stock_name,
            threshold=threshold,
            min_gap=min_gap,
            lookback_days=lookback_days,
            end_date=end_date,
            mysql_cfg=mysql_cfg,
            created_from={
                "ts_code": resolved_ts,
                "name": stock_name,
                "threshold": threshold,
                "min_gap": min_gap,
                "lookback_days": lookback_days,
                "end_date": end_date,
                "asset_type": asset_type,
                "output_format": output_format,
                "template_version": template_version,
                "async_mode": True,
            },
            asset_type=asset_type,
            output_format=output_format,
            template_version=template_version,
        )
        _mark_report_task_done(
            task_id=task_id,
            result=result,
            duration_ms=int((time.time() - started) * 1000),
            mysql_cfg=mysql_cfg,
        )
    except Exception as exc:
        _mark_report_task_error(
            task_id=task_id,
            error_message=str(exc),
            duration_ms=int((time.time() - started) * 1000),
            mysql_cfg=mysql_cfg,
        )


def _submit_report_task(
    *,
    ts_code: Optional[str],
    query_name: Optional[str],
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    mysql_cfg: dict,
    asset_type: str,
    output_format: str,
    template_version: str,
) -> str:
    task_id = uuid.uuid4().hex[:16]
    _insert_report_task(
        task_id=task_id,
        status="queued",
        asset_type=asset_type,
        ts_code=ts_code,
        query_name=query_name,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        end_date=end_date,
        output_format=output_format,
        template_version=template_version,
        mysql_cfg=mysql_cfg,
    )
    REPORT_TASK_EXECUTOR.submit(
        _run_report_task,
        task_id,
        ts_code=ts_code,
        query_name=query_name,
        threshold=threshold,
        min_gap=min_gap,
        lookback_days=lookback_days,
        end_date=end_date,
        mysql_cfg=mysql_cfg,
        asset_type=asset_type,
        output_format=output_format,
        template_version=template_version,
    )
    return task_id


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/suggestions")
def get_asset_suggestions(
    query: str,
    asset_type: str = "S",
    limit: int = 10,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_limit = max(1, min(int(limit), 30))
    try:
        items = suggest_assets(
            query=query,
            asset_type=asset_type,
            limit=safe_limit,
            mysql_connect=lambda: _mysql_connect(mysql_cfg),
        )
        return {
            "query": query,
            "asset_type": (asset_type or "S").strip().upper(),
            "limit": safe_limit,
            "count": len(items),
            "items": items,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/event-suggestions")
def get_event_suggestions(
    query: str,
    limit: int = 12,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_limit = max(1, min(int(limit), 30))
    try:
        items = suggest_assets(
            query=query,
            asset_type="S",
            limit=safe_limit,
            mysql_connect=lambda: _mysql_connect(mysql_cfg),
        )
        return {
            "query": query,
            "asset_type": "S",
            "limit": safe_limit,
            "count": len(items),
            "items": items,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/cycle-events")
def list_cycle_events(
    ts_code: str,
    start_date: str,
    end_date: str,
    asset_type: str = "E",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    try:
        safe_start = _resolve_event_trade_date(start_date)
        safe_end = _resolve_event_trade_date(end_date)
        return {
            "ts_code": ts_code.strip().upper(),
            "asset_type": (asset_type or "E").strip().upper()[:1] or "E",
            "start_date": safe_start,
            "end_date": safe_end,
            "items": _list_cycle_events(
                ts_code=ts_code,
                asset_type=asset_type,
                start_date=safe_start,
                end_date=safe_end,
                mysql_cfg=mysql_cfg,
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/cycle-events")
def create_cycle_event(
    req: EventCreateRequest,
    request: Request,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    creator = (request.client.host if request.client else "") or "ui"
    try:
        item = _create_cycle_event(
            trade_date=req.trade_date,
            title=req.title,
            content=req.content,
            ts_codes=req.ts_codes,
            is_global=bool(req.global_event),
            tags=req.tags,
            source=req.source,
            created_by=creator,
            mysql_cfg=mysql_cfg,
        )
        return {"ok": True, "item": item}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/cycle-events/{event_uuid}")
def delete_cycle_event(
    event_uuid: str = ApiPath(..., description="32-char event uuid"),
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    try:
        deleted = _delete_cycle_event(event_uuid=event_uuid, mysql_cfg=mysql_cfg)
        return {"ok": True, "event_uuid": event_uuid, "deleted": bool(deleted)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reports")
def generate_report(req: GenerateRequest):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=req.mysql_host,
        mysql_port=req.mysql_port,
        mysql_user=req.mysql_user,
        mysql_database=req.mysql_database,
        mysql_password=req.mysql_password,
    )
    safe_type = (req.asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H"}:
        raise HTTPException(status_code=400, detail="asset_type must be E/F/H")
    try:
        if req.async_mode:
            task_id = _submit_report_task(
                ts_code=None,
                query_name=req.name,
                threshold=req.threshold,
                min_gap=req.min_gap,
                lookback_days=req.lookback_days,
                end_date=req.end_date,
                mysql_cfg=mysql_cfg,
                asset_type=safe_type,
                output_format=req.output_format,
                template_version=req.template_version,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "task_id": task_id,
                    "status": "queued",
                    "poll_url": f"/api/report-tasks/{task_id}",
                },
            )
        ts_code, real_name = _resolve_asset_by_name(
            name=req.name,
            mysql_cfg=mysql_cfg,
            asset_type=safe_type,
        )
        return _generate_report_cached(
            ts_code=ts_code,
            stock_name=real_name,
            threshold=req.threshold,
            min_gap=req.min_gap,
            lookback_days=req.lookback_days,
            end_date=req.end_date,
            mysql_cfg=mysql_cfg,
            created_from=req.model_dump(
                exclude={"mysql_host", "mysql_user", "mysql_database", "mysql_password"}
            ),
            asset_type=safe_type,
            output_format=req.output_format,
            template_version=req.template_version,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reports/by-code")
def generate_report_by_code(req: GenerateByCodeRequest):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=req.mysql_host,
        mysql_port=req.mysql_port,
        mysql_user=req.mysql_user,
        mysql_database=req.mysql_database,
        mysql_password=req.mysql_password,
    )
    safe_type = (req.asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H"}:
        raise HTTPException(status_code=400, detail="asset_type must be E/F/H")
    ts_code = req.ts_code.strip().upper()
    try:
        if req.async_mode:
            task_id = _submit_report_task(
                ts_code=ts_code,
                query_name=None,
                threshold=req.threshold,
                min_gap=req.min_gap,
                lookback_days=req.lookback_days,
                end_date=req.end_date,
                mysql_cfg=mysql_cfg,
                asset_type=safe_type,
                output_format=req.output_format,
                template_version=req.template_version,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "task_id": task_id,
                    "status": "queued",
                    "poll_url": f"/api/report-tasks/{task_id}",
                },
            )
        stock_name = _find_asset_name_by_code(ts_code, mysql_cfg, safe_type)
        return _generate_report_cached(
            ts_code=ts_code,
            stock_name=stock_name,
            threshold=req.threshold,
            min_gap=req.min_gap,
            lookback_days=req.lookback_days,
            end_date=req.end_date,
            mysql_cfg=mysql_cfg,
            created_from=req.model_dump(
                exclude={"mysql_host", "mysql_user", "mysql_database", "mysql_password"}
            ),
            asset_type=safe_type,
            output_format=req.output_format,
            template_version=req.template_version,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/uptrend-stocks")
def list_uptrend_stocks(
    request: Request,
    threshold: float = 0.08,
    min_gap: int = 5,
    lookback_days: int = 365,
    min_rows: int = 60,
    top_k: int = 0,
    end_date: Optional[str] = None,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
    async_mode: bool = True,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    if async_mode:
        task_id = _submit_uptrend_task(
            asset_type="E",
            threshold=threshold,
            min_gap=min_gap,
            lookback_days=lookback_days,
            min_rows=min_rows,
            top_k=top_k,
            end_date=end_date,
            request_ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
            mysql_cfg=mysql_cfg,
        )
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "poll_url": f"/api/uptrend-stocks/tasks/{task_id}",
            },
        )

    try:
        return _execute_uptrend_scan(
            threshold=threshold,
            min_gap=min_gap,
            lookback_days=lookback_days,
            min_rows=min_rows,
            top_k=top_k,
            end_date=end_date,
            mysql_cfg=mysql_cfg,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/uptrend-stocks/tasks/{task_id}")
def get_uptrend_task(
    task_id: str,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_uptrend_task(task_id, mysql_cfg)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")

    status = row.get("status")
    base = {
        "task_id": row.get("task_id"),
        "asset_type": row.get("asset_type") or "E",
        "status": status,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "params": {
            "threshold": row.get("threshold"),
            "min_gap": row.get("min_gap"),
            "lookback_days": row.get("lookback_days"),
            "min_rows": row.get("min_rows"),
            "top_k": row.get("top_k"),
            "end_date": row.get("end_date"),
        },
        "poll_url": f"/api/uptrend-stocks/tasks/{task_id}",
        "items_url": f"/api/uptrend-stocks/tasks/{task_id}/items",
    }
    if status in {"queued", "running"}:
        return base
    if status == "error":
        return JSONResponse(
            status_code=500,
            content={
                **base,
                "error": row.get("error_message") or "task failed",
                "duration_ms": row.get("duration_ms"),
            },
        )

    returned = int(row.get("returned_count") or 0)
    page_size = min(1000, max(1, returned or int(row.get("top_k") or 150)))
    _, items = _fetch_uptrend_task_items(task_id=task_id, page=1, page_size=page_size, mysql_cfg=mysql_cfg)
    return {
        **base,
        "count": int(row.get("result_count") or 0),
        "returned": int(row.get("returned_count") or 0),
        "duration_ms": row.get("duration_ms"),
        "items": items,
    }


@app.get("/api/uptrend-stocks/task-history")
def list_uptrend_task_history(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_status = status.strip().lower()
    if safe_status and safe_status not in {"queued", "running", "done", "error"}:
        raise HTTPException(status_code=400, detail="invalid status filter")
    total, rows = _list_uptrend_tasks(
        page=page,
        page_size=page_size,
        status=safe_status,
        asset_type="E",
        mysql_cfg=mysql_cfg,
    )
    items = []
    for row in rows:
        items.append(
            {
                "task_id": row.get("task_id"),
                "asset_type": row.get("asset_type") or "E",
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "duration_ms": row.get("duration_ms"),
                "count": int(row.get("result_count") or 0),
                "returned": int(row.get("returned_count") or 0),
                "error": row.get("error_message"),
                "params": {
                    "threshold": row.get("threshold"),
                    "min_gap": row.get("min_gap"),
                    "lookback_days": row.get("lookback_days"),
                    "min_rows": row.get("min_rows"),
                    "top_k": row.get("top_k"),
                    "end_date": row.get("end_date"),
                },
            }
        )
    return {
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 100)),
        "items": items,
    }


@app.get("/api/uptrend-stocks/tasks/{task_id}/items")
def get_uptrend_task_items(
    task_id: str,
    page: int = 1,
    page_size: int = 200,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_uptrend_task(task_id, mysql_cfg)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    total, items = _fetch_uptrend_task_items(
        task_id=task_id,
        page=page,
        page_size=page_size,
        mysql_cfg=mysql_cfg,
    )
    return {
        "task_id": task_id,
        "status": row.get("status"),
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 1000)),
        "items": items,
    }


@app.get("/api/uptrend-funds")
def list_uptrend_funds(
    request: Request,
    threshold: float = 0.08,
    min_gap: int = 5,
    lookback_days: int = 365,
    min_rows: int = 60,
    top_k: int = 0,
    end_date: Optional[str] = None,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
    async_mode: bool = True,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    if async_mode:
        task_id = _submit_uptrend_task(
            asset_type="F",
            threshold=threshold,
            min_gap=min_gap,
            lookback_days=lookback_days,
            min_rows=min_rows,
            top_k=top_k,
            end_date=end_date,
            request_ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
            mysql_cfg=mysql_cfg,
        )
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "poll_url": f"/api/uptrend-stocks/tasks/{task_id}",
            },
        )
    try:
        return _execute_uptrend_fund_scan(
            threshold=threshold,
            min_gap=min_gap,
            lookback_days=lookback_days,
            min_rows=min_rows,
            top_k=top_k,
            end_date=end_date,
            mysql_cfg=mysql_cfg,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/uptrend-funds/task-history")
def list_uptrend_fund_task_history(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_status = status.strip().lower()
    if safe_status and safe_status not in {"queued", "running", "done", "error"}:
        raise HTTPException(status_code=400, detail="invalid status filter")
    total, rows = _list_uptrend_tasks(
        page=page,
        page_size=page_size,
        status=safe_status,
        asset_type="F",
        mysql_cfg=mysql_cfg,
    )
    items = []
    for row in rows:
        items.append(
            {
                "task_id": row.get("task_id"),
                "asset_type": row.get("asset_type") or "F",
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "duration_ms": row.get("duration_ms"),
                "count": int(row.get("result_count") or 0),
                "returned": int(row.get("returned_count") or 0),
                "error": row.get("error_message"),
                "params": {
                    "threshold": row.get("threshold"),
                    "min_gap": row.get("min_gap"),
                    "lookback_days": row.get("lookback_days"),
                    "min_rows": row.get("min_rows"),
                    "top_k": row.get("top_k"),
                    "end_date": row.get("end_date"),
                },
            }
        )
    return {
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 100)),
        "items": items,
    }


@app.get("/api/market-breadth-daily")
def market_breadth_daily(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    with_report: bool = False,
    async_mode: bool = True,
    sample_points: int = BREADTH_MAX_SAMPLE_POINTS,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    try:
        safe_start, safe_end = _resolve_market_breadth_range(
            start_date=start_date,
            end_date=end_date,
            mysql_cfg=mysql_cfg,
        )
        safe_sample_points = max(50, min(2000, int(sample_points)))
        span_days = (date.fromisoformat(safe_end) - date.fromisoformat(safe_start)).days + 1
        use_async = async_mode and (with_report or span_days >= BREADTH_AUTO_ASYNC_SPAN_DAYS)
        if use_async:
            task_id = _submit_breadth_task(
                start_date=safe_start,
                end_date=safe_end,
                with_report=with_report,
                sample_points=safe_sample_points,
                request_ip=(request.client.host if request.client else ""),
                user_agent=request.headers.get("user-agent", ""),
                mysql_cfg=mysql_cfg,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "task_id": task_id,
                    "status": "queued",
                    "poll_url": f"/api/market-breadth-daily/tasks/{task_id}",
                    "start_date": safe_start,
                    "end_date": safe_end,
                    "sample_points": safe_sample_points,
                    "with_report": with_report,
                },
            )

        result, _metrics = _execute_market_breadth_query(
            start_date=safe_start,
            end_date=safe_end,
            with_report=with_report,
            sample_points=safe_sample_points,
            mysql_cfg=mysql_cfg,
        )
        result["async_mode"] = False
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/market-breadth-daily/tasks/{task_id}")
def get_market_breadth_task(
    task_id: str,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_breadth_task(task_id, mysql_cfg)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    status = str(row.get("status") or "")
    base = {
        "task_id": row.get("task_id"),
        "status": status,
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "with_report": bool(row.get("with_report")),
        "sample_points": int(row.get("sample_points") or 0),
        "poll_url": f"/api/market-breadth-daily/tasks/{task_id}",
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "metrics": {
            "cache_hit": bool(row.get("cache_hit")),
            "query_ms": row.get("query_ms"),
            "render_ms": row.get("render_ms"),
            "total_ms": row.get("total_ms"),
        },
    }
    if status in {"queued", "running"}:
        return base
    if status == "error":
        return JSONResponse(
            status_code=500,
            content={**base, "error": row.get("error_message") or "task failed"},
        )

    payload = _load_breadth_task_result(str(row.get("result_file") or ""))
    if not payload:
        raise HTTPException(status_code=500, detail="task result missing")
    payload["task_id"] = task_id
    payload["status"] = status
    payload["created_at"] = row.get("created_at")
    payload["started_at"] = row.get("started_at")
    payload["finished_at"] = row.get("finished_at")
    payload["metrics"] = base["metrics"]
    payload["async_mode"] = True
    return payload


@app.get("/api/market-breadth-daily/task-history")
def list_market_breadth_task_history(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_status = status.strip().lower()
    if safe_status and safe_status not in {"queued", "running", "done", "error"}:
        raise HTTPException(status_code=400, detail="invalid status filter")
    total, rows = _list_breadth_tasks(
        page=page,
        page_size=page_size,
        status=safe_status,
        mysql_cfg=mysql_cfg,
    )
    items = []
    for row in rows:
        items.append(
            {
                "task_id": row.get("task_id"),
                "status": row.get("status"),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "with_report": bool(row.get("with_report")),
                "sample_points": int(row.get("sample_points") or 0),
                "count": int(row.get("row_count") or 0),
                "report_id": row.get("report_id"),
                "cache_hit": bool(row.get("cache_hit")),
                "metrics": {
                    "query_ms": row.get("query_ms"),
                    "render_ms": row.get("render_ms"),
                    "total_ms": row.get("total_ms"),
                },
                "error": row.get("error_message"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
            }
        )
    return {
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 100)),
        "items": items,
    }


@app.get("/api/factor-incremental")
def factor_incremental(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_k: int = 50,
    min_list_days: int = 60,
    async_mode: bool = True,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_top_k = max(1, min(int(top_k), 500))
    safe_min_list_days = max(0, min(int(min_list_days), 2000))
    safe_start, safe_end = _resolve_factor_research_range(
        start_date=start_date,
        end_date=end_date,
        mysql_cfg=mysql_cfg,
    )
    if async_mode:
        task_id = _submit_research_task(
            task_type="factor_incremental",
            start_date=safe_start,
            end_date=safe_end,
            top_k=safe_top_k,
            min_list_days=safe_min_list_days,
            parameter_trials=0,
            commission_bps=0.0,
            slippage_bps=0.0,
            request_ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
            mysql_cfg=mysql_cfg,
        )
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "task_type": "factor_incremental",
                "poll_url": f"/api/factor-incremental/tasks/{task_id}",
                "start_date": safe_start,
                "end_date": safe_end,
            },
        )
    payload, _metrics = _execute_factor_incremental_query(
        start_date=safe_start,
        end_date=safe_end,
        top_k=safe_top_k,
        min_list_days=safe_min_list_days,
        mysql_cfg=mysql_cfg,
    )
    payload["async_mode"] = False
    return payload


@app.get("/api/factor-incremental/tasks/{task_id}")
def get_factor_incremental_task(
    task_id: str,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_research_task(task_id, mysql_cfg)
    if not row or str(row.get("task_type") or "") != "factor_incremental":
        raise HTTPException(status_code=404, detail="task not found")
    status = str(row.get("status") or "")
    base = {
        "task_id": row.get("task_id"),
        "task_type": "factor_incremental",
        "status": status,
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "params": {
            "top_k": int(row.get("top_k") or 0),
            "min_list_days": int(row.get("min_list_days") or 0),
        },
        "poll_url": f"/api/factor-incremental/tasks/{task_id}",
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "metrics": {
            "query_ms": row.get("query_ms"),
            "total_ms": row.get("total_ms"),
        },
    }
    if status in {"queued", "running"}:
        return base
    if status == "error":
        return JSONResponse(
            status_code=500,
            content={**base, "error": row.get("error_message") or "task failed"},
        )
    payload = _load_research_task_result(str(row.get("result_file") or ""))
    if not payload:
        raise HTTPException(status_code=500, detail="task result missing")
    payload.update(base)
    payload["async_mode"] = True
    return payload


@app.get("/api/factor-incremental/task-history")
def list_factor_incremental_task_history(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_status = status.strip().lower()
    if safe_status and safe_status not in {"queued", "running", "done", "error"}:
        raise HTTPException(status_code=400, detail="invalid status filter")
    total, rows = _list_research_tasks(
        task_type="factor_incremental",
        page=page,
        page_size=page_size,
        status=safe_status,
        mysql_cfg=mysql_cfg,
    )
    return {
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 100)),
        "items": rows,
    }


@app.get("/api/backtest-guardrails")
def backtest_guardrails(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    parameter_trials: int = 10,
    commission_bps: float = 2.0,
    slippage_bps: float = 4.0,
    async_mode: bool = True,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_trials = max(1, min(int(parameter_trials), 2000))
    safe_comm = max(0.0, min(float(commission_bps), 200.0))
    safe_slip = max(0.0, min(float(slippage_bps), 200.0))
    safe_start, safe_end = _resolve_factor_research_range(
        start_date=start_date,
        end_date=end_date,
        mysql_cfg=mysql_cfg,
    )
    if async_mode:
        task_id = _submit_research_task(
            task_type="backtest_guardrail",
            start_date=safe_start,
            end_date=safe_end,
            top_k=0,
            min_list_days=0,
            parameter_trials=safe_trials,
            commission_bps=safe_comm,
            slippage_bps=safe_slip,
            request_ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
            mysql_cfg=mysql_cfg,
        )
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "task_type": "backtest_guardrail",
                "poll_url": f"/api/backtest-guardrails/tasks/{task_id}",
                "start_date": safe_start,
                "end_date": safe_end,
            },
        )
    payload, _metrics = _execute_backtest_guardrail_query(
        start_date=safe_start,
        end_date=safe_end,
        parameter_trials=safe_trials,
        commission_bps=safe_comm,
        slippage_bps=safe_slip,
        mysql_cfg=mysql_cfg,
    )
    payload["async_mode"] = False
    return payload


@app.get("/api/backtest-guardrails/tasks/{task_id}")
def get_backtest_guardrail_task(
    task_id: str,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_research_task(task_id, mysql_cfg)
    if not row or str(row.get("task_type") or "") != "backtest_guardrail":
        raise HTTPException(status_code=404, detail="task not found")
    status = str(row.get("status") or "")
    base = {
        "task_id": row.get("task_id"),
        "task_type": "backtest_guardrail",
        "status": status,
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "params": {
            "parameter_trials": int(row.get("parameter_trials") or 0),
            "commission_bps": float(row.get("commission_bps") or 0),
            "slippage_bps": float(row.get("slippage_bps") or 0),
        },
        "poll_url": f"/api/backtest-guardrails/tasks/{task_id}",
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "metrics": {
            "query_ms": row.get("query_ms"),
            "total_ms": row.get("total_ms"),
        },
    }
    if status in {"queued", "running"}:
        return base
    if status == "error":
        return JSONResponse(
            status_code=500,
            content={**base, "error": row.get("error_message") or "task failed"},
        )
    payload = _load_research_task_result(str(row.get("result_file") or ""))
    if not payload:
        raise HTTPException(status_code=500, detail="task result missing")
    payload.update(base)
    payload["async_mode"] = True
    return payload


@app.get("/api/backtest-guardrails/task-history")
def list_backtest_guardrail_task_history(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    safe_status = status.strip().lower()
    if safe_status and safe_status not in {"queued", "running", "done", "error"}:
        raise HTTPException(status_code=400, detail="invalid status filter")
    total, rows = _list_research_tasks(
        task_type="backtest_guardrail",
        page=page,
        page_size=page_size,
        status=safe_status,
        mysql_cfg=mysql_cfg,
    )
    return {
        "total": total,
        "page": max(1, page),
        "page_size": max(1, min(page_size, 100)),
        "items": rows,
    }


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    return _load_metadata(report_id)


@app.get("/api/report-tasks/{task_id}")
def get_report_task(
    task_id: str,
    mysql_host: str = DEFAULT_MYSQL_HOST,
    mysql_port: int = DEFAULT_MYSQL_PORT,
    mysql_user: str = DEFAULT_MYSQL_USER,
    mysql_database: str = DEFAULT_MYSQL_DB,
    mysql_password: Optional[str] = None,
):
    mysql_cfg = _resolve_mysql_cfg(
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_database=mysql_database,
        mysql_password=mysql_password,
    )
    row = _fetch_report_task(task_id, mysql_cfg)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    status = str(row.get("status") or "")
    base = {
        "task_id": row.get("task_id"),
        "status": status,
        "asset_type": row.get("asset_type") or "E",
        "ts_code": row.get("ts_code"),
        "query_name": row.get("query_name"),
        "params": {
            "threshold": row.get("threshold"),
            "min_gap": row.get("min_gap"),
            "lookback_days": row.get("lookback_days"),
            "end_date": row.get("end_date"),
            "output_format": row.get("output_format"),
            "template_version": row.get("template_version"),
        },
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_ms": row.get("duration_ms"),
        "poll_url": f"/api/report-tasks/{task_id}",
    }
    if status in {"queued", "running"}:
        return base
    if status == "error":
        return JSONResponse(
            status_code=500,
            content={**base, "error": row.get("error_message") or "task failed"},
        )
    report_id = row.get("report_id")
    if not report_id:
        raise HTTPException(status_code=500, detail="report_id missing for done task")
    meta = _load_metadata(str(report_id))
    return {
        **base,
        **meta,
        "report_id": report_id,
        "cache_hit": bool(row.get("cache_hit")),
    }


@app.get("/api/reports/{report_id}/html")
def get_report_html(report_id: str):
    html_path = SERVICE_REPORT_DIR / f"{report_id}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="report html not found")
    return FileResponse(str(html_path), media_type="text/html; charset=utf-8")


@app.get("/api/reports/{report_id}/data")
def get_report_data(report_id: str):
    data_path = SERVICE_REPORT_DIR / f"{report_id}.data.json"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="report data not found")
    try:
        return JSONResponse(content=json.loads(data_path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load report data: {exc}") from exc
