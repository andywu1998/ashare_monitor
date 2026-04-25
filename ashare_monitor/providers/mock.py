"""Mock provider for local testing."""

from __future__ import annotations

from datetime import datetime

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


class MockProvider(BaseProvider):
    """Return deterministic sample data so the pipeline can be exercised offline."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)

    def fetch(self) -> DailyDataset:
        now = datetime.now()
        indices = [
            IndexSnapshot(symbol="000001.SH", name="上证综指", last=3205.3, change=-21.2, change_percent=-0.66, turnover=389.1),
            IndexSnapshot(symbol="399001.SZ", name="深证成指", last=10324.7, change=-155.8, change_percent=-1.49, turnover=519.4),
            IndexSnapshot(symbol="399006.SZ", name="创业板指", last=2085.4, change=-41.5, change_percent=-1.95, turnover=227.0),
            IndexSnapshot(symbol="000688.SH", name="科创50", last=823.9, change=-12.3, change_percent=-1.47, turnover=82.4),
        ]
        breadth = MarketBreadth(advancing=892, declining=3831, unchanged=110, limit_up=34, limit_down=21, consecutive_limit=5)
        top_sectors = [
            SectorMove(name="汽车整车", change_percent=1.8, leaders=["长安汽车", "理想汽车"]),
            SectorMove(name="家电", change_percent=0.9, leaders=["美的集团", "格力电器"]),
            SectorMove(name="电网设备", change_percent=0.6, leaders=["国家电网", "许继电气"]),
        ]
        bottom_sectors = [
            SectorMove(name="计算机应用", change_percent=-3.4, leaders=["用友网络", "金蝶国际"]),
            SectorMove(name="游戏传媒", change_percent=-3.1, leaders=["完美世界", "吉比特"]),
            SectorMove(name="医疗服务", change_percent=-2.8, leaders=["爱尔眼科", "通策医疗"]),
        ]
        reps = [
            RepresentativeStock(symbol="600519.SH", name="贵州茅台", change_percent=-0.8, turnover=9.2),
            RepresentativeStock(symbol="000858.SZ", name="五粮液", change_percent=-1.3, turnover=7.5),
            RepresentativeStock(symbol="601318.SH", name="平安银行", change_percent=0.2, turnover=5.1),
        ]
        capital_flow = CapitalFlow(northbound=-12.3, southbound=None, main_force=-45.0)

        return DailyDataset(
            trade_date=now,
            indices=indices,
            breadth=breadth,
            top_sectors=top_sectors,
            bottom_sectors=bottom_sectors,
            capital_flow=capital_flow,
            representatives=reps,
        )
