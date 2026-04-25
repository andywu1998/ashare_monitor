#!/usr/bin/env python3
"""Entry point for daily A-share monitoring."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import shutil

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ashare_monitor.config import AppConfig, CodexConfig, load_config
from ashare_monitor.data_models import DailyDataset
from ashare_monitor.providers import MockProvider, SinaProvider
from ashare_monitor.providers.base import BaseProvider
from ashare_monitor.reporting import build_codex_prompt, format_text_report

PROVIDERS = {
    "mock": MockProvider,
    "sina": SinaProvider,
    # future providers (tushare/eastmoney) can be registered here
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily A-share monitor scaffolding")
    parser.add_argument(
        "--config",
        default=str(ROOT_DIR / "configs" / "config.toml"),
        help="Path to TOML config",
    )
    parser.add_argument("--provider", help="Override provider name defined in config")
    parser.add_argument("--output-file", help="Optional output path for the text report")
    parser.add_argument("--dump-json", help="Path to dump normalized dataset as JSON")
    parser.add_argument("--codex-enabled", action="store_true", help="Force enable Codex summary")
    parser.add_argument("--codex-disabled", action="store_true", help="Force disable Codex summary")
    parser.add_argument("--dry-run", action="store_true", help="Skip writing files, print only")
    return parser.parse_args()


def resolve_provider(name: str, cfg: AppConfig) -> BaseProvider:
    provider_cls = PROVIDERS.get(name)
    if not provider_cls:
        raise ValueError(f"Unsupported provider: {name}")
    return provider_cls(cfg)


def maybe_call_codex(dataset: DailyDataset, cfg: CodexConfig, enabled: bool) -> Optional[str]:
    if not enabled:
        return None
    codex_cli = shutil.which("codex")
    if not codex_cli:
        return "[warn] 未检测到 codex CLI，跳过AI点评。"

    prompt = build_codex_prompt(dataset)
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cmd = [
        codex_cli,
        "exec",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        str(tmp_path),
        "-m",
        cfg.model,
    ]
    if cfg.reasoning_effort:
        cmd.extend(["-c", f'reasoning_effort="{cfg.reasoning_effort}"'])

    try:
        subprocess.run(
            cmd + [prompt],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=cfg.timeout,
        )
        return tmp_path.read_text(encoding="utf-8").strip()
    except subprocess.TimeoutExpired:
        return f"[warn] Codex 响应超时（>{cfg.timeout:.0f}s）。"
    except subprocess.CalledProcessError as exc:
        return f"[warn] Codex 调用失败 exit={exc.returncode}"
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # pragma: no cover - best effort cleanup
            pass


def dump_json(dataset: DailyDataset, path: Path) -> None:
    payload = asdict(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    provider_name = args.provider or cfg.provider.name
    provider = resolve_provider(provider_name, cfg)

    dataset = provider.fetch()
    base_report = format_text_report(dataset)

    codex_allowed = cfg.codex.enabled
    codex_allowed = codex_allowed or args.codex_enabled
    if args.codex_disabled:
        codex_allowed = False

    codex_summary = maybe_call_codex(dataset, cfg.codex, codex_allowed)

    sections = [base_report]
    if codex_summary:
        sections.extend(["", "=== Codex 专业点评 ===", codex_summary])
    final_text = "\n".join(sections).strip()

    print(final_text)

    trade_date = dataset.trade_date.strftime("%Y%m%d")
    output_path = Path(args.output_file) if args.output_file else cfg.output_dir / f"ashare-{trade_date}.txt"

    if args.dump_json:
        dump_json(dataset, Path(args.dump_json))

    if not args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_text, encoding="utf-8")
        print(f"[saved] {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
