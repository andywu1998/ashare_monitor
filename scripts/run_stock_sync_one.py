#!/usr/bin/env python3
"""Fetch full-history daily data for one stock and upsert into MySQL."""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.stock_sync.fetch_one import main


if __name__ == "__main__":
    main()
