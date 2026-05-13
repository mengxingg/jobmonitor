#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
run_liepin.py — 猎聘独立执行入口

完全独立运行，不依赖其他平台爬虫。
用法:
  python run_liepin.py                          # 完整流程：抓取 → AI评估 → Notion
  python run_liepin.py --dry-run                # 仅抓取，打印前5条原始数据（推荐测试用）
  python run_liepin.py --keyword "AI产品经理"    # 指定搜索关键词
  python run_liepin.py --max-pages 2            # 只翻2页
"""

import sys
import io
import argparse

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from spider_liepin import run as spider_run
from job_model import JobItem


def main():
    parser = argparse.ArgumentParser(description="猎聘独立执行入口")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="仅抓取，打印前5条原始数据（默认开启，安全测试用）")
    parser.add_argument("--full", action="store_true",
                        help="完整模式：抓取 → AI评估 → Notion（覆盖 --dry-run）")
    parser.add_argument("--keyword", type=str, default="",
                        help="搜索关键词（默认使用 spider_liepin.py 中的 KEYWORD）")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="最多翻N页（0=使用 spider_liepin.py 默认值）")
    args = parser.parse_args()

    # --full 覆盖 dry-run
    is_dry_run = not args.full

    print("=" * 60)
    print("🚀 猎聘 独立执行")
    if is_dry_run:
        print("  模式: --dry-run (仅抓取预览，默认安全模式)")
    else:
        print("  模式: --full (完整流程)")
    if args.keyword:
        print(f"  关键词: {args.keyword}")
    if args.max_pages:
        print(f"  最大翻页: {args.max_pages}")
    print("=" * 60)

    # 调用 spider_liepin 核心抓取逻辑
    jobs = spider_run(keyword=args.keyword or None, max_pages=args.max_pages or None)

    if not jobs:
        print("\n❌ 猎聘未获取到任何岗位")
        sys.exit(1)

    print(f"\n📊 共获取到 {len(jobs)} 条岗位")

    if is_dry_run:
        print("\n" + "=" * 60)
        print("📋 前 5 条原始数据预览（dry-run 模式）")
        print("=" * 60)
        for i, job in enumerate(jobs[:5], 1):
            print(f"\n--- [{i}] {job.platform} ---")
            print(f"  岗位: {job.job_name}")
            print(f"  公司: {job.company}")
            print(f"  薪资: {job.salary}")
            print(f"  城市: {job.city}")
            print(f"  URL:  {job.url}")
            print(f"  ID:   {job.platform_job_id}")
        print(f"\n... 共 {len(jobs)} 条，仅预览前 5 条")
        print("💡 提示: 使用 python run_liepin.py --full 执行完整流程")
    else:
        print("\n✅ 完整流程执行完毕（AI评估 + Notion同步已在 spider_liepin 内部完成）")


if __name__ == "__main__":
    main()
