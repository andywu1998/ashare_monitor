#!/usr/bin/env python3
"""Cycle analysis web service (backend)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[2]  # ashare_monitor/
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.run_cycle_report import (
    DbConfig,
    fetch_daily,
    latest_trade_date,
    make_html,
    resolve_stock,
    zigzag_pivots,
)


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
SERVICE_REPORT_DIR = BASE_DIR / "reports" / "service"
SERVICE_REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MYSQL_HOST = os.getenv("MYSQL_HOST", "192.168.1.15")
DEFAULT_MYSQL_USER = os.getenv("MYSQL_USER", "myuser")
DEFAULT_MYSQL_DB = os.getenv("MYSQL_DATABASE", "mydb")
DEFAULT_MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")

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


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/reports")
def generate_report(req: GenerateRequest):
    password = req.mysql_password or DEFAULT_MYSQL_PASSWORD
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
        end_date = req.end_date or latest_trade_date(cfg)
        start_date = (date.fromisoformat(end_date) - timedelta(days=req.lookback_days)).isoformat()
        rows = fetch_daily(ts_code, start_date, end_date, cfg)
        if len(rows) < 20:
            raise HTTPException(status_code=422, detail="not enough data in requested range")
        pivots = zigzag_pivots(rows, req.threshold, req.min_gap)
        html = make_html(real_name, ts_code, rows, pivots, req.threshold, req.min_gap)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    report_id = uuid.uuid4().hex[:12]
    summary = _build_summary(rows, pivots, real_name, ts_code, req.threshold, req.min_gap)
    metadata = {
        "report_id": report_id,
        "summary": summary,
        "html_url": f"/api/reports/{report_id}/html",
        "created_from": req.model_dump(
            exclude={"mysql_host", "mysql_user", "mysql_database", "mysql_password"}
        ),
    }
    _save_report(report_id, html, metadata)
    return metadata


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    return _load_metadata(report_id)


@app.get("/api/reports/{report_id}/html")
def get_report_html(report_id: str):
    html_path = SERVICE_REPORT_DIR / f"{report_id}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="report html not found")
    return FileResponse(str(html_path), media_type="text/html; charset=utf-8")
