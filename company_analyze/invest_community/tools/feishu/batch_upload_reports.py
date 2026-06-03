#!/usr/bin/env python3
"""批量上传 Markdown 文件到飞书文档

遍历指定目录下的所有 .md 文件，逐个上传到飞书。
"""

import os
import sys
import time
import subprocess
from pathlib import Path


def main():
    # 获取报告目录路径
    script_dir = Path(__file__).resolve().parent
    reports_dir = script_dir.parent / "company_report" / "reports"

    if not reports_dir.exists():
        print(f"错误：报告目录不存在: {reports_dir}")
        return 1

    # 获取所有 .md 文件
    md_files = sorted(reports_dir.glob("*.md"))

    if not md_files:
        print(f"未找到任何 .md 文件: {reports_dir}")
        return 1

    print(f"找到 {len(md_files)} 个 Markdown 文件")
    print("=" * 60)

    # 统计信息
    success_count = 0
    failed_count = 0
    failed_files = []

    # 逐个上传
    for idx, md_file in enumerate(md_files, 1):
        print(f"\n[{idx}/{len(md_files)}] 处理文件: {md_file.name}")
        print("-" * 60)

        try:
            # 设置环境变量指定要处理的文件
            env = os.environ.copy()
            env["FEISHU_MARKDOWN_PATH"] = str(md_file)

            # 调用上传脚本
            result = subprocess.run(
                [sys.executable, str(script_dir / "create_doc_from_md.py")],
                env=env,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                print(f"✓ 成功上传: {md_file.name}")
                # 从输出中提取文档链接
                for line in result.stdout.splitlines():
                    if "文档链接:" in line:
                        print(f"  {line.strip()}")
                success_count += 1
            else:
                print(f"✗ 上传失败: {md_file.name}")
                print(f"  错误信息: {result.stderr[-200:]}")
                failed_count += 1
                failed_files.append(md_file.name)

        except subprocess.TimeoutExpired:
            print(f"✗ 上传超时: {md_file.name}")
            failed_count += 1
            failed_files.append(md_file.name)

        except Exception as e:
            print(f"✗ 处理异常: {md_file.name}")
            print(f"  异常信息: {str(e)}")
            failed_count += 1
            failed_files.append(md_file.name)

        # 添加延迟以避免 API 限流
        if idx < len(md_files):
            delay = float(os.getenv("FEISHU_BATCH_DELAY", "2.0"))
            print(f"等待 {delay} 秒...")
            time.sleep(delay)

    # 输出统计信息
    print("\n" + "=" * 60)
    print("批量上传完成")
    print("=" * 60)
    print(f"总文件数: {len(md_files)}")
    print(f"成功: {success_count}")
    print(f"失败: {failed_count}")

    if failed_files:
        print("\n失败的文件:")
        for filename in failed_files:
            print(f"  - {filename}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
