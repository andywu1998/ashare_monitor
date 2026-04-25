"""Sina-based provider fetching live A-share data without authentication."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from ..config import AppConfig
from ..data_models import (
    CapitalFlow,
    DailyDataset,
    IndexSnapshot,
    MarketBreadth,
    RepresentativeStock,
    SectorMove,
)
from .base import BaseProvider

_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ",
    "Referer": "https://finance.sina.com.cn/",
}


class SinaProvider(BaseProvider):
    STOCK_API = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?num={page_size}&page={page}&sort=changepercent&asc=0&node=hs_a"
    )
    SECTOR_API = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    INDEX_API = "https://hq.sinajs.cn/list={codes}"
    TENCENT_QUOTE_API = "https://qt.gtimg.cn/q={codes}"
    KAMT_API = (
        "https://push2.eastmoney.com/api/qt/kamt/get?fields1=f1,f2,f3,f4&"
        "fields2=f51,f52,f53,f54&ut=b2884a393a59ad64002292a3e90d46a5"
    )

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)
        extras = config.provider.extras or {}
        self.timeout = float(extras.get("timeout", 8))
        self.hot_size = int(extras.get("hot_size", 3))
        self.xueqiu_cookie = extras.get("xueqiu_cookie") or os.getenv("XUEQIU_COOKIE")
        self._stock_cache: List[dict] = []

    # ------------------------------ public API ------------------------------
    def fetch(self) -> DailyDataset:
        stock_rows = self._fetch_stock_rows()
        stock_map = self._build_stock_map(stock_rows)
        indices = self._fetch_indices()
        breadth = self._build_breadth(stock_rows)
        top_sectors, bottom_sectors = self._fetch_sector_moves()
        capital_flow = self._fetch_capital_flow(stock_rows)
        representatives = self._build_representatives(stock_map)

        return DailyDataset(
            trade_date=datetime.now(timezone.utc),
            indices=indices,
            breadth=breadth,
            top_sectors=top_sectors,
            bottom_sectors=bottom_sectors,
            capital_flow=capital_flow,
            representatives=representatives,
        )

    # ------------------------------ helpers ---------------------------------
    def _http_get(self, url: str, encoding: str = "utf-8") -> str:
        req = urllib.request.Request(url, headers=_SINA_HEADERS)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec: B310
            data = resp.read()
        return data.decode(encoding, errors="ignore")

    def _fetch_stock_rows(self) -> List[dict]:
        if self._stock_cache:
            return self._stock_cache
        requested_size = int(self.config.provider.extras.get("page_size", 100))
        page_size = max(1, min(requested_size, 100))
        max_pages = int(self.config.provider.extras.get("max_pages", 80))
        rows: List[dict] = []
        for page in range(1, max_pages + 1):
            url = self.STOCK_API.format(page_size=page_size, page=page)
            try:
                chunk = json.loads(self._http_get(url))
            except Exception:  # pragma: no cover
                break
            if not isinstance(chunk, list) or not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
        self._stock_cache = rows
        return rows

    def _build_stock_map(self, rows: List[dict]) -> Dict[str, dict]:
        mapping: Dict[str, dict] = {}
        for row in rows:
            raw = (row.get("symbol") or "").lower()
            if not raw or len(raw) <= 2:
                continue
            prefix, code = raw[:2], raw[2:]
            suffix = prefix.upper()
            normalized = f"{code.upper()}.{suffix}" if suffix in {"SH", "SZ", "BJ"} else raw.upper()
            mapping[normalized] = row
        return mapping

    def _fetch_indices(self) -> List[IndexSnapshot]:
        order: List[str] = []
        codes: List[str] = []
        for ticker in self.config.report.indices:
            symbol = ticker.upper()
            tencent_code = self._to_tencent_symbol(symbol)
            if not tencent_code:
                continue
            order.append(symbol)
            codes.append(tencent_code)
        if not codes:
            return []
        url = self.TENCENT_QUOTE_API.format(codes=",".join(codes))
        text = self._http_get(url, encoding="gbk")
        snapshots: Dict[str, IndexSnapshot] = {}
        for raw_line in text.strip().splitlines():
            if "=" not in raw_line:
                continue
            ident, payload = raw_line.split("=", 1)
            code = ident.replace("v_", "").strip()
            payload = payload.strip().strip(";").strip('"')
            fields = payload.split("~")
            if len(fields) < 6:
                continue
            last = float(fields[3] or 0.0)
            prev = float(fields[4] or 0.0)
            change = last - prev
            pct = (change / prev * 100) if prev else 0.0
            turnover = None
            if len(fields) > 35 and "/" in fields[35]:
                parts = fields[35].split("/")
                if len(parts) >= 3:
                    try:
                        turnover = float(parts[2]) / 1e8
                    except ValueError:
                        turnover = None
            volume = None
            if len(fields) > 36:
                try:
                    volume = float(fields[36])
                except ValueError:
                    volume = None
            symbol = self._format_index_symbol(code)
            snapshots[symbol] = IndexSnapshot(
                symbol=symbol,
                name=fields[1] or symbol,
                last=last,
                change=change,
                change_percent=pct,
                turnover=turnover,
                volume=volume,
            )
        return [snapshots[s] for s in order if s in snapshots]

    def _format_index_symbol(self, raw: str) -> str:
        if raw.startswith("sh"):
            return f"{raw[2:].upper()}.SH"
        if raw.startswith("sz"):
            return f"{raw[2:].upper()}.SZ"
        return raw.upper()

    def _to_tencent_symbol(self, symbol: str) -> str | None:
        base = symbol.split(".")[0]
        if symbol.endswith(".SH"):
            return f"sh{base}"
        if symbol.endswith(".SZ"):
            return f"sz{base}"
        if symbol.endswith(".BJ"):
            return f"bj{base}"
        return None

    def _build_breadth(self, rows: List[dict]) -> MarketBreadth:
        advancing = declining = unchanged = limit_up = limit_down = 0
        for row in rows:
            pct = float(row.get("changepercent") or 0.0)
            if pct > 0:
                advancing += 1
            elif pct < 0:
                declining += 1
            else:
                unchanged += 1
            if pct >= 9.9:
                limit_up += 1
            if pct <= -9.9:
                limit_down += 1
        return MarketBreadth(
            advancing=advancing,
            declining=declining,
            unchanged=unchanged,
            limit_up=limit_up,
            limit_down=limit_down,
            consecutive_limit=None,
        )

    def _fetch_sector_moves(self) -> tuple[List[SectorMove], List[SectorMove]]:
        text = self._http_get(self.SECTOR_API, encoding="gbk")
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return [], []
        raw = text[start : end + 1]
        data = json.loads(raw)
        sectors: List[SectorMove] = []
        for value in data.values():
            parts = value.split(",")
            if len(parts) < 6:
                continue
            name = parts[1]
            try:
                change_pct = float(parts[5])
            except ValueError:
                change_pct = 0.0
            leader = parts[-1] if len(parts) >= 13 else ""
            sectors.append(SectorMove(name=name, change_percent=change_pct, leaders=[leader] if leader else []))
        sectors.sort(key=lambda s: s.change_percent, reverse=True)
        k = max(1, self.config.report.sector_top_k)
        top = sectors[:k]
        bottom = sorted(sectors, key=lambda s: s.change_percent)[:k]
        return top, bottom

    def _fetch_capital_flow(self, rows: List[dict]) -> CapitalFlow:
        northbound = southbound = None
        try:
            data = json.loads(self._http_get(self.KAMT_API))
            payload = data.get("data") or {}
            hk2sh = float(payload.get("hk2sh", {}).get("dayNetAmtIn", 0.0))
            hk2sz = float(payload.get("hk2sz", {}).get("dayNetAmtIn", 0.0))
            sh2hk = float(payload.get("sh2hk", {}).get("dayNetAmtIn", 0.0))
            sz2hk = float(payload.get("sz2hk", {}).get("dayNetAmtIn", 0.0))
            northbound = (hk2sh + hk2sz) / 10000 if (hk2sh or hk2sz) else None
            southbound = (sh2hk + sz2hk) / 10000 if (sh2hk or sz2hk) else None
        except Exception:  # pragma: no cover - network issues fallback
            northbound = southbound = None

        # Use aggregate turnover as a proxy for main-force flow (in billions CNY)
        total_amount = sum(float(row.get("amount") or 0.0) for row in rows)
        main_force = total_amount / 1e8 if total_amount else None
        return CapitalFlow(northbound=northbound, southbound=southbound, main_force=main_force)

    def _build_representatives(self, stock_map: Dict[str, dict]) -> List[RepresentativeStock]:
        targets = [code.upper() for code in (self.config.report.representatives or []) if code]
        if not targets:
            targets = self._fetch_hot_symbols()
        if not targets:
            targets = self._top_turnover_symbols(self.hot_size or 3)
        quotes = self._fetch_quote_snapshots(targets)
        reps: List[RepresentativeStock] = []
        for code in targets:
            snap = quotes.get(code)
            if not snap:
                row = stock_map.get(code)
                if not row:
                    continue
                pct = float(row.get("changepercent") or 0.0)
                turnover = float(row.get("amount") or 0.0) / 1e8 if row.get("amount") else None
                reps.append(
                    RepresentativeStock(
                        symbol=code,
                        name=row.get("name", code),
                        change_percent=pct,
                        turnover=turnover,
                    )
                )
                continue
            reps.append(
                RepresentativeStock(
                    symbol=code,
                    name=snap["name"],
                    change_percent=snap["pct"],
                    turnover=snap["turnover"],
                )
            )
        return reps or [
            RepresentativeStock(symbol="N/A", name="暂无代表性个股", change_percent=0.0, turnover=None)
        ]

    def _top_turnover_symbols(self, limit: int) -> List[str]:
        if not self._stock_cache:
            return []
        ranked = sorted(
            (row for row in self._stock_cache if row.get("amount")),
            key=lambda r: float(r.get("amount") or 0.0),
            reverse=True,
        )
        symbols: List[str] = []
        for row in ranked:
            raw = (row.get("symbol") or "").lower()
            if not raw:
                continue
            prefix, code = raw[:2], raw[2:]
            suffix = prefix.upper()
            normalized = f"{code.upper()}.{suffix}" if suffix in {"SH", "SZ", "BJ"} else raw.upper()
            if normalized not in symbols:
                symbols.append(normalized)
            if len(symbols) >= max(1, limit):
                break
        return symbols

    def _fetch_hot_symbols(self) -> List[str]:
        if not self.xueqiu_cookie or self.hot_size <= 0:
            return []
        url = (
            "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
            f"?region=CN&type=10&size={self.hot_size}"
        )
        headers = {
            "User-Agent": _SINA_HEADERS["User-Agent"],
            "Cookie": self.xueqiu_cookie,
            "Accept": "application/json",
            "Referer": "https://xueqiu.com/",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec: B310
                payload = resp.read().decode("utf-8")
        except Exception:
            return []
        try:
            data = json.loads(payload)
            items = (data.get("data") or {}).get("items") or []
        except Exception:
            return []
        symbols: List[str] = []
        for item in items:
            symbol = (item.get("symbol") or "").upper()
            if symbol.startswith("SH"):
                symbols.append(f"{symbol[2:]}.SH")
            elif symbol.startswith("SZ"):
                symbols.append(f"{symbol[2:]}.SZ")
            elif symbol.startswith("BJ"):
                symbols.append(f"{symbol[2:]}.BJ")
        return symbols

    def _fetch_hot_symbols(self) -> List[str]:
        if not self.xueqiu_cookie or self.hot_size <= 0:
            return []
        url = (
            "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
            f"?region=CN&type=10&size={self.hot_size}"
        )
        headers = {
            "User-Agent": _SINA_HEADERS["User-Agent"],
            "Cookie": self.xueqiu_cookie,
            "Accept": "application/json",
            "Referer": "https://xueqiu.com/",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec: B310
                payload = resp.read().decode("utf-8")
        except Exception:
            return []
        try:
            data = json.loads(payload)
            items = (data.get("data") or {}).get("items") or []
        except Exception:
            return []
        symbols: List[str] = []
        for item in items:
            symbol = (item.get("symbol") or "").upper()
            if symbol.startswith("SH"):
                symbols.append(f"{symbol[2:]}.SH")
            elif symbol.startswith("SZ"):
                symbols.append(f"{symbol[2:]}.SZ")
            elif symbol.startswith("BJ"):
                symbols.append(f"{symbol[2:]}.BJ")
        return symbols

    def _top_turnover_symbols(self, limit: int) -> List[str]:
        if not self._stock_cache:
            return []
        ranked = sorted(
            (row for row in self._stock_cache if row.get("amount")),
            key=lambda r: float(r.get("amount") or 0.0),
            reverse=True,
        )
        symbols: List[str] = []
        for row in ranked:
            raw = (row.get("symbol") or "").lower()
            if not raw:
                continue
            prefix, code = raw[:2], raw[2:]
            suffix = prefix.upper()
            normalized = f"{code.upper()}.{suffix}" if suffix in {"SH", "SZ", "BJ"} else raw.upper()
            if normalized not in symbols:
                symbols.append(normalized)
            if len(symbols) >= max(1, limit):
                break
        return symbols

    def _fetch_quote_snapshots(self, symbols: List[str]) -> Dict[str, dict]:
        clean = [s for s in symbols if s]
        if not clean:
            return {}
        prefixes = []
        for sym in clean:
            base, suffix = sym.split(".")[0], sym.split(".")[-1]
            prefix = "sh" if suffix == "SH" else "sz" if suffix == "SZ" else "bj" if suffix == "BJ" else ""
            if not prefix:
                continue
            prefixes.append(f"{prefix}{base}")
        if not prefixes:
            return {}
        url = self.INDEX_API.format(codes=",".join(prefixes))
        text = self._http_get(url, encoding="gbk")
        snapshots: Dict[str, dict] = {}
        for raw_line in text.strip().splitlines():
            if "=" not in raw_line:
                continue
            ident, payload = raw_line.split("=", 1)
            code = ident.replace("var hq_str_", "").strip()
            payload = payload.strip().strip(";").strip('"')
            fields = payload.split(",")
            if len(fields) < 4:
                continue
            symbol = self._format_index_symbol(code)
            try:
                last = float(fields[3])
            except (ValueError, IndexError):
                last = 0.0
            prev = float(fields[2] or 0.0)
            change = last - prev
            pct = (change / prev * 100) if prev else 0.0
            turnover = None
            if len(fields) > 9:
                try:
                    turnover = float(fields[9]) / 1e8
                except ValueError:
                    turnover = None
            snapshots[symbol] = {"name": fields[0] or symbol, "pct": pct, "turnover": turnover}
        return snapshots


__all__ = ["SinaProvider"]
