#!/usr/bin/env python3
"""Cycle analysis web service (backend)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
import json
import os
import pymysql
import sys
from threading import Lock
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[2]  # ashare_monitor/
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ashare_monitor.cycle import find_uptrend_stocks
from scripts.run_cycle_report import (
    DbConfig,
    fetch_daily,
    latest_trade_date,
    make_html,
    run_mysql,
    resolve_stock,
    zigzag_pivots,
)


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
SERVICE_REPORT_DIR = BASE_DIR / "reports" / "service"
SERVICE_REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MYSQL_HOST = os.getenv("CYCLE_WEB_MYSQL_HOST", "192.168.1.15")
DEFAULT_MYSQL_USER = os.getenv("CYCLE_WEB_MYSQL_USER", os.getenv("MYSQL_USER", "myuser"))
DEFAULT_MYSQL_DB = os.getenv("CYCLE_WEB_MYSQL_DATABASE", os.getenv("MYSQL_DATABASE", "mydb"))
DEFAULT_MYSQL_PASSWORD = os.getenv("CYCLE_WEB_MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
DEFAULT_MYSQL_PORT = int(os.getenv("CYCLE_WEB_MYSQL_PORT", os.getenv("MYSQL_PORT", "3306")))
UPTREND_TASK_WORKERS = max(1, int(os.getenv("UPTREND_TASK_WORKERS", "2")))

UPTREND_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=UPTREND_TASK_WORKERS)
UPTREND_TABLE_LOCK = Lock()
UPTREND_TABLE_READY: set[tuple[str, int, str, str]] = set()


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
        conn.commit()

    with UPTREND_TABLE_LOCK:
        UPTREND_TABLE_READY.add(key)


def _insert_uptrend_task(
    *,
    task_id: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    min_rows: int,
    top_k: int,
    end_date: Optional[str],
    request_ip: str,
    user_agent: str,
    mysql_cfg: dict,
) -> None:
    _ensure_uptrend_tables(mysql_cfg)
    sql = """
    INSERT INTO uptrend_scan_task (
      task_id, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
      request_ip, user_agent
    ) VALUES (
      %s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    task_id,
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
      task_id, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
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
    mysql_cfg: dict,
) -> tuple[int, list[dict]]:
    _ensure_uptrend_tables(mysql_cfg)
    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 100))
    offset = (safe_page - 1) * safe_size
    where = ""
    args: list[Any] = []
    if status:
        where = "WHERE status = %s"
        args.append(status)
    count_sql = f"SELECT COUNT(*) AS total FROM uptrend_scan_task {where}"
    list_sql = f"""
    SELECT
      task_id, status, threshold, min_gap, lookback_days, min_rows, top_k, end_date,
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


def _run_uptrend_scan_task(
    task_id: str,
    *,
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

app = FastAPI(title="Cycle Report Service", version="0.1.0")
app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


class GenerateRequest(BaseModel):
    name: str = Field(..., description="Stock name, e.g. 中际旭创")
    threshold: float = Field(0.08, ge=0.005, le=0.8, description="ZigZag threshold")
    min_gap: int = Field(5, ge=1, le=60, description="Minimum trade-day gap between pivots")
    lookback_days: int = Field(365, ge=60, le=3650)
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default latest trade date")
    mysql_host: str = DEFAULT_MYSQL_HOST
    mysql_user: str = DEFAULT_MYSQL_USER
    mysql_database: str = DEFAULT_MYSQL_DB
    mysql_password: Optional[str] = Field(
        None, description="If omitted, uses MYSQL_PASSWORD env var"
    )


class GenerateByCodeRequest(BaseModel):
    ts_code: str = Field(..., description="Stock ts_code, e.g. 300308.SZ")
    threshold: float = Field(0.08, ge=0.005, le=0.8, description="ZigZag threshold")
    min_gap: int = Field(5, ge=1, le=60, description="Minimum trade-day gap between pivots")
    lookback_days: int = Field(365, ge=60, le=3650)
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default latest trade date")
    mysql_host: str = DEFAULT_MYSQL_HOST
    mysql_user: str = DEFAULT_MYSQL_USER
    mysql_database: str = DEFAULT_MYSQL_DB
    mysql_password: Optional[str] = Field(
        None, description="If omitted, uses MYSQL_PASSWORD env var"
    )


class UptrendQuery(BaseModel):
    threshold: float = Field(0.08, ge=0.005, le=0.8)
    min_gap: int = Field(5, ge=1, le=60)
    lookback_days: int = Field(365, ge=60, le=3650)
    min_rows: int = Field(60, ge=20, le=2000)
    top_k: int = Field(0, ge=0, le=20000, description="0 means all")
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD; default today")


def _build_summary(rows, pivots, stock_name: str, ts_code: str, threshold: float, min_gap: int):
    if not rows:
        return {}
    prices = [float(r.get("close") or 0) for r in rows]
    latest = rows[-1]
    cycle_count = max(0, len(pivots) - 1)
    return {
        "stock_name": stock_name,
        "ts_code": ts_code,
        "date_range": [str(rows[0].get("trade_date")), str(rows[-1].get("trade_date"))],
        "sample_days": len(rows),
        "min_close": round(min(prices), 4),
        "max_close": round(max(prices), 4),
        "latest_date": str(latest.get("trade_date")),
        "latest_close": round(float(latest.get("close") or 0), 4),
        "latest_open": round(float(latest.get("open") or latest.get("close") or 0), 4),
        "latest_high": round(float(latest.get("high") or latest.get("close") or 0), 4),
        "latest_low": round(float(latest.get("low") or latest.get("close") or 0), 4),
        "latest_pct_chg": round(float(latest.get("pct_chg") or 0), 4),
        "latest_vol": float(latest.get("vol") or 0),
        "latest_amount": float(latest.get("amount") or 0),
        "pivot_count": len(pivots),
        "cycle_count": cycle_count,
        "threshold": threshold,
        "min_gap": min_gap,
    }


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


def _find_stock_name_by_code(ts_code: str, cfg: DbConfig) -> str:
    sql = (
        "SELECT name FROM stock_basic "
        f"WHERE ts_code = '{ts_code}' "
        "LIMIT 1;"
    )
    rows = run_mysql(sql, cfg)
    if not rows:
        raise HTTPException(status_code=404, detail=f"stock not found by ts_code: {ts_code}")
    return rows[0].strip()


def _generate_report_impl(
    *,
    ts_code: str,
    stock_name: str,
    threshold: float,
    min_gap: int,
    lookback_days: int,
    end_date: Optional[str],
    cfg: DbConfig,
    created_from: dict,
):
    end_trade_date = end_date or latest_trade_date(cfg)
    start_date = (date.fromisoformat(end_trade_date) - timedelta(days=lookback_days)).isoformat()
    rows = fetch_daily(ts_code, start_date, end_trade_date, cfg)
    if len(rows) < 20:
        raise HTTPException(status_code=422, detail="not enough data in requested range")
    pivots = zigzag_pivots(rows, threshold, min_gap)
    html = make_html(stock_name, ts_code, rows, pivots, threshold, min_gap)

    report_id = uuid.uuid4().hex[:12]
    summary = _build_summary(rows, pivots, stock_name, ts_code, threshold, min_gap)
    metadata = {
        "report_id": report_id,
        "summary": summary,
        "html_url": f"/api/reports/{report_id}/html",
        "created_from": created_from,
    }
    _save_report(report_id, html, metadata)
    return metadata


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/reports")
def generate_report(req: GenerateRequest):
    password = _normalize_password(req.mysql_password) or _normalize_password(DEFAULT_MYSQL_PASSWORD)
    if not password:
        raise HTTPException(
            status_code=400,
            detail="mysql password missing; pass mysql_password or set MYSQL_PASSWORD env",
        )

    cfg = DbConfig(
        host=req.mysql_host,
        user=req.mysql_user,
        password=password,
        database=req.mysql_database,
    )
    try:
        ts_code, real_name = resolve_stock(req.name, cfg)
        return _generate_report_impl(
            ts_code=ts_code,
            stock_name=real_name,
            threshold=req.threshold,
            min_gap=req.min_gap,
            lookback_days=req.lookback_days,
            end_date=req.end_date,
            cfg=cfg,
            created_from=req.model_dump(
                exclude={"mysql_host", "mysql_user", "mysql_database", "mysql_password"}
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reports/by-code")
def generate_report_by_code(req: GenerateByCodeRequest):
    password = _normalize_password(req.mysql_password) or _normalize_password(DEFAULT_MYSQL_PASSWORD)
    if not password:
        raise HTTPException(
            status_code=400,
            detail="mysql password missing; pass mysql_password or set MYSQL_PASSWORD env",
        )

    cfg = DbConfig(
        host=req.mysql_host,
        user=req.mysql_user,
        password=password,
        database=req.mysql_database,
    )
    ts_code = req.ts_code.strip().upper()
    try:
        stock_name = _find_stock_name_by_code(ts_code, cfg)
        return _generate_report_impl(
            ts_code=ts_code,
            stock_name=stock_name,
            threshold=req.threshold,
            min_gap=req.min_gap,
            lookback_days=req.lookback_days,
            end_date=req.end_date,
            cfg=cfg,
            created_from=req.model_dump(
                exclude={"mysql_host", "mysql_user", "mysql_database", "mysql_password"}
            ),
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
        mysql_cfg=mysql_cfg,
    )
    items = []
    for row in rows:
        items.append(
            {
                "task_id": row.get("task_id"),
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


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    return _load_metadata(report_id)


@app.get("/api/reports/{report_id}/html")
def get_report_html(report_id: str):
    html_path = SERVICE_REPORT_DIR / f"{report_id}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="report html not found")
    return FileResponse(str(html_path), media_type="text/html; charset=utf-8")
