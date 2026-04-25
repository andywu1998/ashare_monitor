"""Typed containers for normalized A-share metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass(slots=True)
class IndexSnapshot:
    symbol: str
    name: str
    last: float
    change: float
    change_percent: float
    turnover: Optional[float] = None  # in CNY billions
    volume: Optional[float] = None  # in shares or lots, provider-specific


@dataclass(slots=True)
class MarketBreadth:
    advancing: int
    declining: int
    unchanged: int
    limit_up: int
    limit_down: int
    consecutive_limit: Optional[int] = None


@dataclass(slots=True)
class SectorMove:
    name: str
    change_percent: float
    leaders: List[str] = field(default_factory=list)


@dataclass(slots=True)
class CapitalFlow:
    northbound: Optional[float] = None  # CNY billions
    southbound: Optional[float] = None
    main_force: Optional[float] = None


@dataclass(slots=True)
class RepresentativeStock:
    symbol: str
    name: str
    change_percent: float
    turnover: Optional[float] = None


@dataclass(slots=True)
class DailyDataset:
    trade_date: datetime
    indices: List[IndexSnapshot]
    breadth: MarketBreadth
    top_sectors: List[SectorMove]
    bottom_sectors: List[SectorMove]
    capital_flow: CapitalFlow
    representatives: List[RepresentativeStock]


__all__ = [
    "IndexSnapshot",
    "MarketBreadth",
    "SectorMove",
    "CapitalFlow",
    "RepresentativeStock",
    "DailyDataset",
]
