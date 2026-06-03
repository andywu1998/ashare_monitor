#!/usr/bin/env python3
"""Batch-generate company reports by invoking generate_company_report.py."""

import os
import subprocess
import sys


COMPANIES = [
"比亚迪"
]


def main() -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    generator = os.path.join(script_dir, "generate_company_report.py")

    python = sys.executable or "python"

    for company in COMPANIES:
        if not company.strip():
            continue
        print(f"开始生成：{company}")
        result = subprocess.run([python, generator, company], check=False)
        if result.returncode != 0:
            print(f"生成失败：{company} (exit {result.returncode})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
