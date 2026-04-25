"""Configuration loading helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Python 3.11+ with tomllib is required for config parsing") from exc


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(slots=True)
class ProviderConfig:
    name: str = "sina"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReportConfig:
    indices: List[str] = field(default_factory=lambda: ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH"])
    sector_top_k: int = 3
    representatives: List[str] = field(default_factory=list)


@dataclass(slots=True)
class CodexConfig:
    enabled: bool = False
    model: str = "gpt-5.3-codex"
    reasoning_effort: str = "medium"
    timeout: float = 60.0


@dataclass(slots=True)
class AppConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    output_dir: Path = field(default_factory=lambda: _expand("reports"))


def load_config(path: str | Path) -> AppConfig:
    file_path = _expand(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Config not found: {file_path}")

    data = tomllib.loads(file_path.read_text(encoding="utf-8"))

    provider_data = data.get("provider", {})
    report_data = data.get("report", {})
    codex_data = data.get("codex", {})
    output_dir = _expand(data.get("output_dir", str(file_path.parent / "reports")))

    provider = ProviderConfig(
        name=provider_data.get("name", "mock"),
        api_key=provider_data.get("api_key"),
        base_url=provider_data.get("base_url"),
        extras={k: v for k, v in provider_data.items() if k not in {"name", "api_key", "base_url"}},
    )

    report = ReportConfig(
        indices=report_data.get("indices") or ReportConfig().indices,
        sector_top_k=int(report_data.get("sector_top_k", 3)),
        representatives=report_data.get("representatives", []),
    )

    codex = CodexConfig(
        enabled=bool(codex_data.get("enabled", False)),
        model=codex_data.get("model", "gpt-5.3-codex"),
        reasoning_effort=codex_data.get("reasoning_effort", "medium"),
        timeout=float(codex_data.get("timeout", 60.0)),
    )

    cfg = AppConfig(provider=provider, report=report, codex=codex, output_dir=output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return cfg


__all__ = ["AppConfig", "ProviderConfig", "ReportConfig", "CodexConfig", "load_config"]
