"""Reusable asset suggestion helpers for cycle_web."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_text(value: str) -> str:
    """Normalize text for fuzzy matching across full/half-width variants."""
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", text).upper()


def to_fullwidth_ascii(value: str) -> str:
    out: list[str] = []
    for ch in value or "":
        code = ord(ch)
        if ch == " ":
            out.append("\u3000")
        elif 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def build_query_variants(query: str) -> list[str]:
    raw = (query or "").strip()
    if not raw:
        return []

    variants: list[str] = []
    for item in (
        raw,
        unicodedata.normalize("NFKC", raw),
        raw.upper(),
        to_fullwidth_ascii(raw),
    ):
        value = (item or "").strip()
        if value and value not in variants:
            variants.append(value)

    # Remove trailing alnum suffix to tolerate queries like "万科A" / "ETF500A".
    trimmed = re.sub(r"[A-Za-z0-9]+$", "", unicodedata.normalize("NFKC", raw)).strip()
    if trimmed and trimmed not in variants:
        variants.append(trimmed)
    return variants


def _score_candidate(query_norm: str, row: dict[str, Any]) -> tuple[int, int, str]:
    name_norm = normalize_text(str(row.get("name") or ""))
    symbol_norm = normalize_text(str(row.get("symbol") or ""))
    code_norm = normalize_text(str(row.get("ts_code") or ""))
    if not query_norm:
        return (99, 99, code_norm)

    if code_norm == query_norm:
        score = 0
    elif symbol_norm == query_norm:
        score = 1
    elif name_norm == query_norm:
        score = 2
    elif name_norm.startswith(query_norm):
        score = 3
    elif query_norm in name_norm:
        score = 4
    elif symbol_norm.startswith(query_norm):
        score = 5
    elif query_norm in symbol_norm:
        score = 6
    elif code_norm.startswith(query_norm):
        score = 7
    elif query_norm in code_norm:
        score = 8
    else:
        score = 50
    return (score, abs(len(name_norm) - len(query_norm)), code_norm)


def _build_where_clause(variants: list[str]) -> tuple[str, list[str]]:
    if not variants:
        return "", []
    clauses: list[str] = []
    args: list[str] = []
    for token in variants:
        like = f"%{token}%"
        clauses.append("(name LIKE %s OR symbol LIKE %s OR ts_code LIKE %s)")
        args.extend([like, like, like])
    return " AND (" + " OR ".join(clauses) + ")", args


def suggest_assets(
    *,
    query: str,
    asset_type: str,
    limit: int,
    mysql_connect,
) -> list[dict[str, Any]]:
    """Suggest stocks/funds from stock_basic with robust name matching."""
    safe_type = (asset_type or "E").strip().upper()
    if safe_type not in {"E", "F", "H", "S"}:
        raise ValueError("asset_type must be E/F/H/S")

    query = (query or "").strip()
    if not query:
        return []

    variants = build_query_variants(query)
    where_ext, args = _build_where_clause(variants)
    where_asset = "asset_type = %s"
    sql_args: list[Any]
    if safe_type == "S":
        where_asset = "asset_type IN ('E','H')"
        sql_args = []
    else:
        sql_args = [safe_type]

    sql = f"""
    SELECT ts_code, symbol, name, asset_type
    FROM stock_basic
    WHERE {where_asset}
      AND list_status = 'L'
      {where_ext}
    ORDER BY ts_code
    LIMIT %s
    """
    sql_args.extend(args)
    sql_args.append(max(20, limit * 8))
    with mysql_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(sql_args))
            rows = cursor.fetchall() or []

    query_norm = normalize_text(query)
    ranked = sorted(rows, key=lambda r: _score_candidate(query_norm, r))
    out: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for row in ranked:
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code or ts_code in seen_codes:
            continue
        seen_codes.add(ts_code)
        name = str(row.get("name") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        out.append(
            {
                "ts_code": ts_code,
                "symbol": symbol,
                "name": name,
                "asset_type": str(row.get("asset_type") or safe_type),
                "display": f"{name} ({ts_code})",
                "value": name,
            }
        )
        if len(out) >= limit:
            break
    return out
