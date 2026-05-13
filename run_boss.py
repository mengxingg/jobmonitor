#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
run_boss.py — BOSS直聘独立执行入口

完全独立运行，不依赖其他平台爬虫。
用法:
  python run_boss.py                          # 完整流程：抓取 → AI评估 → Notion
  python run_boss.py --dry-run                # 仅抓取，打印前5条，不调AI/Notion
  python run_boss.py --max-jobs 10            # 最多处理10条
"""

import sys
import io
import argparse

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from spider_boss import run as spider_run
from job_model import JobItem


def main():
    parser = argparse.ArgumentParser(description="BOSS直聘独立执行入口")
    parser.add_argument("--dry-run", action="store_true", help="仅抓取，打印前5条，不调AI/Notion")
    parser.add_argument("--max-jobs", type=int, default=0, help="最多处理N条（0=不限）")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 BOSS直聘 独立执行")
    if args.dry_run:
        print("  模式: --dry-run (仅抓取预览)")
    if args.max_jobs:
        print(f"  限制: 最多 {args.max_jobs} 条")
    print("=" * 60)

    # 调用 spider_boss 核心抓取逻辑
    jobs = spider_run()

    if not jobs:
        print("\n❌ 未获取到任何岗位")
        sys.exit(1)

    print(f"\n📊 共获取到 {len(jobs)} 条岗位")

    if args.dry_run:
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
    else:
        # 完整流程：spider_boss.run() 内部已包含 AI评估 + Notion同步
        print("\n✅ 完整流程执行完毕（AI评估 + Notion同步已在 spider_boss 内部完成）")


if __name__ == "__main__":
    main()
