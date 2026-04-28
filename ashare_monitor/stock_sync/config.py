import os
from pathlib import Path
import shlex
import subprocess

def load_env_from_zshrc(override: bool = False) -> None:
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    try:
        cmd = [
            "zsh",
            "-lc",
            f"source {shlex.quote(str(zshrc))} >/dev/null 2>&1; env -0",
        ]
        proc = subprocess.run(cmd, check=True, capture_output=True)
    except Exception:
        return
    for chunk in proc.stdout.split(b"\x00"):
        if not chunk or b"=" not in chunk:
            continue
        key_bytes, value_bytes = chunk.split(b"=", 1)
        key = key_bytes.decode("utf-8", errors="ignore")
        if not key:
            continue
        value = value_bytes.decode("utf-8", errors="ignore")
        if override or key not in os.environ:
            os.environ[key] = value


load_env_from_zshrc(override=False)


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
