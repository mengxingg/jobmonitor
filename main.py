"""
job_engine 主入口

完整流程：抓取 → AI 评估 → Notion 同步

用法:
    # 导航到 TARGET_URL 抓取岗位列表
    python main.py

    # 仅抓取 + AI 评估，不写入 Notion（调试用）
    python main.py --dry-run

    # 仅打印结果到控制台
    python main.py --print-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from scraper import JobItem, scrape_boss_list
from ai_matcher import evaluate_job
from notion_sync import sync_job

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="job_engine: 读取当前 Chrome 标签页 → AI 匹配 → Notion 同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python main.py\n  python main.py --dry-run\n  python main.py --print-only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅读取和 AI 评估，不写入 Notion",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="仅打印结果到控制台，不写入 Notion",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    # 日志级别
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── 1. 读取当前页面 ──
    logger.info("=" * 50)
    logger.info("读取当前 Chrome 标签页中的岗位列表...")
    logger.info("=" * 50)

    scraped_jobs: list[JobItem] = scrape_boss_list()

    if not scraped_jobs:
        logger.warning("未读取到任何岗位，退出")
        sys.exit(0)

    logger.info("读取到 %s 个岗位，开始 AI 评估...", len(scraped_jobs))

    # ── 2. AI 评估 ──
    results: list[dict] = []
    for idx, job in enumerate(scraped_jobs, 1):
        logger.info("[%s/%s] 评估: %s - %s", idx, len(scraped_jobs), job.company, job.title)

        ai_result = evaluate_job(
            title=job.title,
            company=job.company,
            salary=job.salary,
            location=job.location,
            platform=job.platform,
        )

        record = {
            **asdict(job),
            "match_score": ai_result["score"],
            "notes": ai_result["reason"],
        }
        results.append(record)

        # 打印单条结果
        score_str = f"{ai_result['score']:3d}分"
        print(f"  [{score_str}] {job.company} - {job.title} | {job.salary} | {ai_result['reason']}")

    # ── 3. 输出 / 同步 ──
    if args.print_only or args.dry_run:
        print("\n" + "=" * 50)
        print(f"共 {len(results)} 个岗位（{'仅打印' if args.print_only else 'DRY RUN，未写入 Notion'}）")
        print("=" * 50)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # ── 4. 写入 Notion ──
    logger.info("=" * 50)
    logger.info("开始同步到 Notion...")
    logger.info("=" * 50)

    success = 0
    failed = 0
    for idx, record in enumerate(results, 1):
        logger.info("[%s/%s] 同步: %s - %s", idx, len(results), record["company"], record["title"])
        ok = sync_job(
            title=record["title"],
            company=record["company"],
            platform=record["platform"],
            url=record["url"],
            location=record["location"],
            salary_range=record["salary"],
            match_score=record["match_score"],
            notes=record["notes"],
        )
        if ok:
            success += 1
        else:
            failed += 1

    print("\n" + "=" * 50)
    print(f"✅ 完成！成功: {success} / 失败: {failed} / 总计: {len(results)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
