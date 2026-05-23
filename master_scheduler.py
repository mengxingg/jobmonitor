#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
master_scheduler.py — InterviewOS 全链路总调度入口

组合任务 (Workflow):
  第一步：调用 scheduler.py 执行全平台爬虫抓取 + Notion 同步
          （爬虫报错时 send_scraper_alarm 会正常拦截告警，不阻断主流程）
  第二步：爬虫全部执行完毕后，调用 daily_briefing.py 的 trigger_daily_briefing()
          生成早报并推送到飞书

用法:
  conda run -n job_env python master_scheduler.py                          # 完整流程：抓取 → 早报
  conda run -n job_env python master_scheduler.py --no-crawl               # 仅推送早报（跳过抓取）
  conda run -n job_env python master_scheduler.py --no-briefing            # 仅抓取（跳过早报）
  conda run -n job_env python master_scheduler.py --chat_id=oc_xxxxx       # 指定早报推送会话
  conda run -n job_env python master_scheduler.py --dry-run                # 仅抓取预览，不推送

数据流:
  scheduler.run_all_spiders() → 全平台爬虫（BOSS直聘、猎聘、腾讯、字节、DeepSeek、小红书等）
  scheduler.run_bridge()     → AI 评分 + Notion 同步
  daily_briefing.trigger_daily_briefing() → 查询 Notion 高分岗位 → AI 提炼 → 飞书早报推送
"""

import sys
import io
import time
import logging
import os
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("master_scheduler")

PROJECT_DIR = Path(__file__).parent

# ── 加载 .env ──
from dotenv import load_dotenv
_env_path = PROJECT_DIR / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

BRIEFING_CHAT_ID = os.getenv("BRIEFING_CHAT_ID", "")


# ==========================================
# 第一步：全平台爬虫抓取 + Notion 同步
# ==========================================


def step_crawl_and_sync() -> bool:
    """
    执行全平台爬虫抓取 + Notion 同步。

    直接复用 scheduler.py 的 run_scrapers() 逻辑：
      - 串行运行所有爬虫（三方平台 + 官网）
      - 爬虫报错时 send_scraper_alarm 会正常拦截告警
      - 错误不会阻断主流程，其他平台继续执行
      - 最后统一触发 openclaw_bridge.py 推送到 Notion

    Returns:
        True 表示至少部分爬虫成功，False 表示完全失败
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("📡 [Step 1/2] 全平台爬虫抓取 + Notion 同步")
    logger.info("=" * 60)

    try:
        # 导入 scheduler 模块
        from scheduler import run_scrapers

        # 执行全量同步（no_bridge=False，即抓取后自动触发 Notion 同步）
        run_scrapers(no_bridge=False)

        logger.info("✅ [Step 1/2] 全平台抓取 + Notion 同步完成")
        return True

    except Exception as e:
        logger.error(f"❌ [Step 1/2] 全平台抓取出错: {e}", exc_info=True)
        # 不阻断主流程，返回 False 但继续执行早报
        return False


# ==========================================
# 第二步：生成并推送早报
# ==========================================


def step_daily_briefing(chat_id: str) -> bool:
    """
    生成每日早报并推送到飞书。

    直接调用 daily_briefing.py 的 run_daily_briefing() 逻辑：
      1. 查询 Notion 过去 24 小时新增高分岗位（Match Score >= 80）
      2. 如果无新增高分岗位，静默退出，不打扰用户
      3. 有则调用 AI 提炼核心匹配点，组装极客风格早报卡片
      4. 通过飞书 API 推送格式化早报

    Args:
        chat_id: 飞书会话 ID

    Returns:
        True 表示成功推送，False 表示失败或无数据
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("☀️ [Step 2/2] 生成并推送每日早报")
    logger.info("=" * 60)

    if not chat_id:
        logger.error("❌ [Step 2/2] 未指定早报推送目标会话 ID")
        logger.error("   请通过以下方式之一配置：")
        logger.error("   1. 在 .env 中设置 BRIEFING_CHAT_ID=oc_xxxxx")
        logger.error("   2. 命令行参数: --chat_id=oc_xxxxx")
        return False

    try:
        # 导入 daily_briefing 模块
        from daily_briefing import run_daily_briefing

        success = run_daily_briefing(chat_id)

        if success:
            logger.info("✅ [Step 2/2] 早报推送完成")
        else:
            logger.info("⏭️ [Step 2/2] 早报跳过（无新增高分岗位或推送失败）")

        return success

    except Exception as e:
        logger.error(f"❌ [Step 2/2] 早报推送出错: {e}", exc_info=True)
        return False


# ==========================================
# 全链路入口
# ==========================================


def run_full_workflow(
    chat_id: str,
    skip_crawl: bool = False,
    skip_briefing: bool = False,
    dry_run: bool = False,
) -> None:
    """
    执行全链路工作流：抓取 → 同步 → 早报推送。

    Args:
        chat_id: 飞书会话 ID（早报推送目标）
        skip_crawl: 是否跳过爬虫抓取步骤
        skip_briefing: 是否跳过早报推送步骤
        dry_run: 是否仅预览（仅影响爬虫阶段行为）
    """
    start_time = time.time()

    print(f"\n{'=' * 60}")
    print(f"🚀 InterviewOS 全链路总调度")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    if dry_run:
        print("  模式: --dry-run (仅抓取预览，不推送早报)")
    if skip_crawl:
        print("  模式: --no-crawl (跳过抓取，仅推送早报)")
    if skip_briefing:
        print("  模式: --no-briefing (跳过早报，仅抓取)")

    print(f"  早报推送会话: {chat_id or '未配置'}")
    print(f"{'=' * 60}\n")

    # ── 第一步：爬虫抓取 + Notion 同步 ──
    crawl_ok = True
    if not skip_crawl:
        if dry_run:
            # dry-run 模式：仅执行爬虫抓取，不触发 Notion 同步
            logger.info("🔄 --dry-run 模式：仅执行爬虫抓取预览")
            try:
                from scheduler import run_all_spiders
                run_all_spiders()
                logger.info("✅ 爬虫抓取预览完成")
            except Exception as e:
                logger.error(f"❌ 爬虫抓取出错: {e}", exc_info=True)
                crawl_ok = False
        else:
            crawl_ok = step_crawl_and_sync()
    else:
        logger.info("⏭️ 跳过爬虫抓取步骤 (--no-crawl)")

    # ── 第二步：早报推送（放在 finally 中确保执行） ──
    briefing_ok = False
    try:
        if not skip_briefing and not dry_run:
            briefing_ok = step_daily_briefing(chat_id)
        elif dry_run:
            logger.info("⏭️ --dry-run 模式，跳过早报推送")
        else:
            logger.info("⏭️ 跳过早报推送步骤 (--no-briefing)")
    except Exception as e:
        logger.error(f"❌ 早报推送步骤异常: {e}", exc_info=True)
        briefing_ok = False

    # ── 汇总 ──
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"📊 全链路执行汇总")
    print(f"{'=' * 60}")
    print(f"  爬虫抓取: {'✅ 完成' if crawl_ok else '⚠️ 部分失败'}")
    if not skip_briefing and not dry_run:
        print(f"  早报推送: {'✅ 已执行' if briefing_ok else '⚠️ 已执行（无数据或推送失败）'}")
    print(f"  总耗时: {elapsed:.0f} 秒")
    print(f"  完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")



# ==========================================
# 命令行入口
# ==========================================


def main():
    parser = argparse.ArgumentParser(
        description="InterviewOS 全链路总调度入口 — 抓取 → 同步 → 早报推送",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  conda run -n job_env python master_scheduler.py                     # 完整流程\n"
            "  conda run -n job_env python master_scheduler.py --no-crawl          # 仅推送早报\n"
            "  conda run -n job_env python master_scheduler.py --no-briefing       # 仅抓取\n"
            "  conda run -n job_env python master_scheduler.py --chat_id=oc_xxxxx  # 指定会话\n"
            "  conda run -n job_env python master_scheduler.py --dry-run           # 仅抓取预览\n"
        ),
    )
    parser.add_argument(
        "--no-crawl", action="store_true",
        help="跳过爬虫抓取步骤，仅推送早报",
    )
    parser.add_argument(
        "--no-briefing", action="store_true",
        help="跳过早报推送步骤，仅执行爬虫抓取",
    )
    parser.add_argument(
        "--chat_id", type=str, default="",
        help="早报推送目标飞书会话 ID（覆盖 .env 中的 BRIEFING_CHAT_ID）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅执行爬虫抓取预览，不触发 Notion 同步，不推送早报",
    )
    args = parser.parse_args()

    # 确定早报推送目标会话 ID
    chat_id = args.chat_id or BRIEFING_CHAT_ID

    # 执行全链路工作流
    run_full_workflow(
        chat_id=chat_id,
        skip_crawl=args.no_crawl,
        skip_briefing=args.no_briefing,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
