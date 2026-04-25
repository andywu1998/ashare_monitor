#!/usr/bin/env python3
"""Concurrent full-history stock sync with retry and alert strategy."""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.stock_sync.fetch_all_concurrent import main


if __name__ == "__main__":
    main()
