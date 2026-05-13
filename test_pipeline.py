"""
测试流水线：读取 data/test_jobs.json → AI 评估 → Notion 同步

用法:
    python test_pipeline.py              # 完整流程（AI 评估 + Notion 同步）
    python test_pipeline.py --dry-run    # 仅 AI 评估，不写入 Notion
    python test_pipeline.py --print-only # 仅打印结果到控制台
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ai_matcher import evaluate_job
from notion_sync import sync_job

logger = logging.getLogger(__name__)

# ── 数据文件路径 ──

DATA_FILE = Path(__file__).parent / "data" / "test_jobs.json"


def load_jobs() -> list[dict]:
    """从 data/test_jobs.json 读取岗位数据"""
    if not DATA_FILE.exists():
        logger.error("数据文件不存在: %s", DATA_FILE)
        sys.exit(1)

    with open(DATA_FILE, encoding="utf-8") as f:
        jobs = json.load(f)

    if not isinstance(jobs, list):
        logger.error("数据文件格式错误：应为 JSON 数组")
        sys.exit(1)

    logger.info("从 %s 读取到 %s 个岗位", DATA_FILE, len(jobs))
    return jobs


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="测试流水线：data/test_jobs.json → AI 评估 → Notion 同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python test_pipeline.py\n  python test_pipeline.py --dry-run\n  python test_pipeline.py --print-only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅 AI 评估，不写入 Notion",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="仅打印原始数据到控制台，不调用 AI 也不写入 Notion",
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

    # ── 1. 读取数据 ──
    jobs = load_jobs()

    logger.info("=" * 50)
    logger.info("测试流水线启动：共 %s 个岗位", len(jobs))
    logger.info("=" * 50)

    # ── 2. 仅打印原始数据 ──
    if args.print_only:
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
        print(f"\n共 {len(jobs)} 个岗位（仅打印原始数据）")
        return

    # ── 3. AI 评估 ──
    results: list[dict] = []

    for idx, job in enumerate(jobs, 1):
        logger.info("[%s/%s] 评估: %s - %s", idx, len(jobs), job["company"], job["title"])

        ai_result = evaluate_job(
            title=job["title"],
            company=job["company"],
            salary=job["salary"],
            location=job["location"],
            platform=job["platform"],
            jd_summary=job.get("job_summary", ""),
        )

        record = {
            **job,
            "match_score": ai_result["score"],
            "match_reasons": ai_result.get("match_reasons", []),
            "mismatch_reasons": ai_result.get("mismatch_reasons", []),
            "notes": ai_result.get("summary", ""),
            "jd_summary": job.get("job_summary", ""),
        }

        results.append(record)

        # 打印单条结果
        score_str = f"{ai_result['score']:3d}分"
        print(f"  [{score_str}] {job['company']} - {job['title']} | {job['salary']} | {ai_result.get('summary', '')}")

    # ── 4. 汇总统计 ──
    high = sum(1 for r in results if r["match_score"] >= 80)
    mid = sum(1 for r in results if 60 <= r["match_score"] < 80)
    low = sum(1 for r in results if r["match_score"] < 60)

    print()
    print("=" * 50)
    print("📊 匹配度汇总")
    print("=" * 50)
    print(f"  高匹配 (≥80分): {high} 个")
    print(f"  中匹配 (60-79分): {mid} 个")
    print(f"  低匹配 (<60分): {low} 个")
    print(f"  总计: {len(results)} 个")
    print("=" * 50)

    # ── 5. DRY RUN 则退出 ──
    if args.dry_run:
        print("\n完整结果（DRY RUN，未写入 Notion）:")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # ── 6. 写入 Notion ──
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
            remote=record.get("remote", ""),
            salary_range=record["salary"],
            jd_summary=record.get("jd_summary", ""),
            match_score=record["match_score"],
            match_reasons=record.get("match_reasons"),
            mismatch_reasons=record.get("mismatch_reasons"),
            status=record.get("status", "新发现"),
            priority=record.get("priority", ""),
            discovered_date=record.get("discovered_date", ""),
            notes=record["notes"],
        )
        if ok:
            success += 1
        else:
            failed += 1

    print()
    print("=" * 50)
    print(f"✅ 完成！成功: {success} / 失败: {failed} / 总计: {len(results)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
