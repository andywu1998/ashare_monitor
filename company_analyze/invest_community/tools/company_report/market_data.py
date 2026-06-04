"""Fetch recent price context for company reports."""

from __future__ import annotations

import os
import shlex
import subprocess
import contextlib
import io
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


CACHE_DIR = Path(__file__).resolve().parents[3] / "record" / "market_data_cache"
HK_SPOT_CACHE_FILE = CACHE_DIR / "hk_spot_akshare_sina.json"
HK_SPOT_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class StockMatch:
    ts_code: str
    name: str
    market_type: str
    data_source: str


def _load_env_from_zshrc() -> None:
    if os.getenv("TUSHARE_TOKEN"):
        return
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    try:
        proc = subprocess.run(
            [
                "zsh",
                "-lc",
                f"source {shlex.quote(str(zshrc))} >/dev/null 2>&1; env -0",
            ],
            check=True,
            capture_output=True,
        )
    except Exception:
        return
    for chunk in proc.stdout.split(b"\x00"):
        if not chunk or b"=" not in chunk:
            continue
        key, value = chunk.split(b"=", 1)
        key_text = key.decode("utf-8", errors="ignore")
        if key_text and key_text not in os.environ:
            os.environ[key_text] = value.decode("utf-8", errors="ignore")


def _load_simple_exports_from_zshrc() -> None:
    if os.getenv("TUSHARE_TOKEN"):
        return
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    try:
        lines = zshrc.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("export "):
            continue
        try:
            parts = shlex.split(stripped[len("export ") :], posix=True)
        except ValueError:
            continue
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value


def _normalize_name(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("\u3000", "").strip().lower()


def _init_tushare():
    _load_env_from_zshrc()
    _load_simple_exports_from_zshrc()
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("缺少 TUSHARE_TOKEN")
    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError("当前虚拟环境缺少 tushare，请先安装 tushare") from exc
    ts.set_token(token)
    return ts.pro_api()


def _records(df) -> list[dict[str, Any]]:
    if isinstance(df, list):
        return [row for row in df if isinstance(row, dict)]
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _find_in_rows(
    company: str,
    rows: list[dict[str, Any]],
    market_type: str,
    *,
    allow_fuzzy: bool,
) -> StockMatch | None:
    target = _normalize_name(company)
    if not target:
        return None

    candidates: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        ts_code = str(row.get("ts_code") or "").strip()
        if not name or not ts_code:
            continue
        normalized = _normalize_name(name)
        if normalized == target:
            candidates.append(row)
        elif allow_fuzzy and (target in normalized or normalized in target):
            candidates.append(row)

    picked = (candidates or [None])[0]
    if not picked:
        return None
    return StockMatch(
        ts_code=str(picked.get("ts_code") or "").strip(),
        name=str(picked.get("name") or "").strip(),
        market_type=market_type,
        data_source="tushare" if market_type == "A股" else "akshare_sina",
    )


def _get_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("当前虚拟环境缺少 akshare，请先安装 akshare") from exc
    return ak


def _quiet_call(func, *args, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(*args, **kwargs)


def _read_hk_spot_cache() -> list[dict[str, Any]] | None:
    try:
        if not HK_SPOT_CACHE_FILE.exists():
            return None
        payload = json.loads(HK_SPOT_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("created_at", 0)) > HK_SPOT_CACHE_TTL_SECONDS:
            return None
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    except Exception:
        return None
    return None


def _write_hk_spot_cache(rows: list[dict[str, Any]]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"created_at": time.time(), "source": "akshare_sina", "rows": rows}
        HK_SPOT_CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return


def _load_hk_rows_akshare() -> list[dict[str, Any]]:
    cached = _read_hk_spot_cache()
    if cached is not None:
        return cached

    ak = _get_akshare()
    df = _quiet_call(ak.stock_hk_spot)
    rows = []
    for row in _records(df):
        symbol = str(row.get("代码") or row.get("symbol") or "").strip().zfill(5)
        name = str(row.get("中文名称") or row.get("名称") or row.get("name") or "").strip()
        if not symbol or not name:
            continue
        rows.append({"ts_code": f"{symbol}.HK", "name": name})
    _write_hk_spot_cache(rows)
    return rows


def _resolve_stock(pro, company: str) -> StockMatch | None:
    a_rows = _records(
        pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,name,market,list_status",
        )
    )

    try:
        hk_rows = _load_hk_rows_akshare()
    except Exception:
        hk_rows = []

    # Prefer exact name matches across markets before falling back to fuzzy matches.
    for rows, market_type in ((a_rows, "A股"), (hk_rows, "港股")):
        match = _find_in_rows(company, rows, market_type, allow_fuzzy=False)
        if match:
            return match
    for rows, market_type in ((a_rows, "A股"), (hk_rows, "港股")):
        match = _find_in_rows(company, rows, market_type, allow_fuzzy=True)
        if match:
            return match
    return None


def _fetch_daily(pro, match: StockMatch, days: int):
    end = date.today()
    # Use a wider calendar window to cover holidays and weekends.
    start = end - timedelta(days=max(days * 3, 90))
    params = {
        "ts_code": match.ts_code,
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
    }
    if match.market_type == "港股":
        return _fetch_hk_daily_akshare_sina(match.ts_code, start, end)
    return pro.daily(**params)


def _parse_trade_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        import pandas as pd
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y%m%d")
    except Exception:
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            return text
        return text.replace("-", "")


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _fetch_hk_daily_akshare_sina(ts_code: str, start: date, end: date) -> list[dict[str, Any]]:
    ak = _get_akshare()
    symbol = str(ts_code).split(".")[0].zfill(5)
    df = _quiet_call(ak.stock_hk_daily, symbol=symbol, adjust="")
    normalized = []
    previous_close: float | None = None
    for row in sorted(_records(df), key=lambda item: str(_first_value(item, ("date", "日期", "trade_date")) or "")):
        trade_date = _parse_trade_date(_first_value(row, ("date", "日期", "trade_date")))
        if not trade_date:
            continue
        trade_day = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))
        if trade_day < start or trade_day > end:
            continue

        open_px = _float_value(row, "open")
        high_px = _float_value(row, "high")
        low_px = _float_value(row, "low")
        close_px = _float_value(row, "close")
        if open_px is None:
            open_px = _float_value({"value": _first_value(row, ("开盘",))}, "value")
        if high_px is None:
            high_px = _float_value({"value": _first_value(row, ("最高",))}, "value")
        if low_px is None:
            low_px = _float_value({"value": _first_value(row, ("最低",))}, "value")
        if close_px is None:
            close_px = _float_value({"value": _first_value(row, ("收盘",))}, "value")

        change = _float_value(row, "change")
        if change is None:
            change = _float_value({"value": _first_value(row, ("涨跌额",))}, "value")
        pct_chg = _float_value(row, "pct_chg")
        if pct_chg is None:
            pct_chg = _float_value({"value": _first_value(row, ("涨跌幅",))}, "value")
        if pct_chg is None and close_px is not None and previous_close not in (None, 0):
            pct_chg = (close_px / previous_close - 1) * 100

        normalized.append(
            {
                "trade_date": trade_date,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "change": change,
                "pct_chg": pct_chg,
                "vol": _float_value(row, "volume") or _float_value(row, "vol") or _float_value({"value": _first_value(row, ("成交量",))}, "value"),
                "amount": _float_value(row, "amount") or _float_value({"value": _first_value(row, ("成交额",))}, "value"),
            }
        )
        if close_px is not None:
            previous_close = close_px
    return normalized


def _float_value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _format_amount(value: float | None) -> str:
    if value is None:
        return "-"
    # Tushare A-share amount is usually in thousand CNY; HK may differ by endpoint.
    return f"{value:.0f}"


def _format_price_context(company: str, match: StockMatch, rows: list[dict[str, Any]]) -> str:
    rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    closes = [_float_value(row, "close") for row in rows]
    closes = [value for value in closes if value is not None]
    highs = [_float_value(row, "high") for row in rows]
    lows = [_float_value(row, "low") for row in rows]
    amounts = [_float_value(row, "amount") for row in rows]
    amounts = [value for value in amounts if value is not None]

    start_close = closes[0] if closes else None
    end_close = closes[-1] if closes else None
    pct_change = None
    if start_close and end_close is not None:
        pct_change = (end_close / start_close - 1) * 100

    valid_highs = [value for value in highs if value is not None]
    valid_lows = [value for value in lows if value is not None]
    avg_amount = sum(amounts) / len(amounts) if amounts else None

    lines = [
        "## 最近30个交易日股价数据",
        f"- 公司匹配：{match.name}（{match.ts_code}，{match.market_type}）",
        f"- 行情数据源：{match.data_source}",
        f"- 数据条数：{len(rows)}",
    ]
    if rows:
        lines.append(
            f"- 数据区间：{rows[0].get('trade_date')} 至 {rows[-1].get('trade_date')}"
        )
    lines.extend(
        [
            f"- 区间收盘涨跌幅：{_format_number(pct_change)}%",
            f"- 区间最高价：{_format_number(max(valid_highs) if valid_highs else None)}",
            f"- 区间最低价：{_format_number(min(valid_lows) if valid_lows else None)}",
            f"- 平均成交额/成交金额字段：{_format_amount(avg_amount)}",
            "",
            "| 日期 | 开盘 | 最高 | 最低 | 收盘 | 涨跌幅% | 成交量 | 成交额/金额 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for row in rows:
        lines.append(
            "| {date} | {open} | {high} | {low} | {close} | {pct} | {vol} | {amount} |".format(
                date=row.get("trade_date") or "-",
                open=_format_number(_float_value(row, "open")),
                high=_format_number(_float_value(row, "high")),
                low=_format_number(_float_value(row, "low")),
                close=_format_number(_float_value(row, "close")),
                pct=_format_number(_float_value(row, "pct_chg")),
                vol=_format_amount(_float_value(row, "vol")),
                amount=_format_amount(_float_value(row, "amount")),
            )
        )

    lines.extend(
        [
            "",
            "请在研报的“近30天股价表现与事件催化”部分优先使用以上行情数据；",
            "如果公开信息与上述行情数据冲突，以上述行情数据为准，并说明冲突点；",
            "引用日期时必须原样使用上表的 YYYYMMDD 日期，不得自行改写年份或推断为其他年份。",
        ]
    )
    return "\n".join(lines)


def build_recent_price_context(company: str, days: int = 30) -> str:
    try:
        pro = _init_tushare()
        match = _resolve_stock(pro, company)
        if not match:
            return (
                "## 最近30个交易日股价数据\n"
                f"- 未能根据公司名“{company}”在 A 股/港股基础信息中匹配证券代码。\n"
                "- 请在研报中将近30天股价表现标注为“未知/需验证”。\n"
                "- 禁止编造或举例任何行情日期。"
            )
        df = _fetch_daily(pro, match, days)
        rows = _records(df)
        rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""), reverse=True)[:days]
        if not rows:
            return (
                "## 最近30个交易日股价数据\n"
                f"- 已匹配：{match.name}（{match.ts_code}，{match.market_type}），但未获取到最近行情。\n"
                "- 请在研报中将近30天股价表现标注为“未知/需验证”。\n"
                "- 禁止编造或举例任何行情日期。"
            )
        return _format_price_context(company, match, rows)
    except Exception as exc:
        return (
            "## 最近30个交易日股价数据\n"
            f"- 获取失败：{exc}\n"
            "- 请在研报中将近30天股价表现标注为“未知/需验证”。\n"
            "- 禁止编造或举例任何行情日期。"
        )
