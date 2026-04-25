"""Base provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..config import AppConfig
from ..data_models import DailyDataset


class BaseProvider(ABC):
    """Abstract provider interface."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch(self) -> DailyDataset:
        """Return the normalized dataset for the current trade date."""

    @staticmethod
    def ensure_required_fields(fields: Sequence[str], payload: dict) -> None:
        missing = [field for field in fields if field not in payload]
        if missing:
            raise ValueError(f"Provider payload missing required fields: {', '.join(missing)}")
