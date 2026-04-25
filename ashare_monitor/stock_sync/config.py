import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
SYNC_ENV = ROOT_DIR / "configs" / "stock_sync.env"
load_dotenv(SYNC_ENV, override=False)


TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", ""),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", ""),
    "charset": "utf8mb4",
    "autocommit": False,
}
