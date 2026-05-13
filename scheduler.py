"""
scheduler.py — 多平台爬虫调度器

串行调用 spider_boss 和 spider_liepin，将标准化 JobItem 统一合并后，
统一发送给 DeepSeek 评估和 Notion 同步。

用法:
  python scheduler.py                    # 立即执行一轮
  pm2 start ... -- scheduler.py          # PM2 托管定时执行
"""

import sys
import io
import time
import logging
from datetime import datetime
from typing import Optional

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from job_model import JobItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


def run_all_spiders() -> list[JobItem]:
    """
    串行运行所有爬虫，返回合并后的标准化 JobItem 列表。
    每个爬虫独立管理自己的浏览器生命周期。
    """
    all_jobs: list[JobItem] = []

    # ── 1. BOSS 直聘 ──
    try:
        from spider_boss import run as run_boss
        logger.info("=" * 60)
        logger.info("🚀 启动 BOSS 直聘爬虫...")
        boss_jobs = run_boss()
        if boss_jobs:
            all_jobs.extend(boss_jobs)
            logger.info("✅ BOSS 直聘完成: %d 条", len(boss_jobs))
        else:
            logger.warning("⚠ BOSS 直聘返回 0 条")
    except Exception as e:
        logger.error("❌ BOSS 直聘爬虫异常: %s", e, exc_info=True)

    # ── 2. 猎聘 ──
    try:
        from spider_liepin import run as run_liepin
        logger.info("=" * 60)
        logger.info("🚀 启动猎聘爬虫...")
        liepin_jobs = run_liepin()
        if liepin_jobs:
            all_jobs.extend(liepin_jobs)
            logger.info("✅ 猎聘完成: %d 条", len(liepin_jobs))
        else:
            logger.warning("⚠ 猎聘返回 0 条")
    except Exception as e:
        logger.error("❌ 猎聘爬虫异常: %s", e, exc_info=True)

    # ── 汇总 ──
    logger.info("=" * 60)
    logger.info("📊 本轮汇总: 共 %d 条岗位（BOSS %d + 猎聘 %d）",
                len(all_jobs),
                sum(1 for j in all_jobs if j.platform == "BOSS直聘"),
                sum(1 for j in all_jobs if j.platform == "猎聘"))

    return all_jobs


def run_scrapers():
    """定时任务入口：串行执行所有爬虫"""
    print(f"\n{'='*60}")
    print(f"========== {datetime.now()} 启动本轮多平台抓取 ==========")
    print(f"{'='*60}")

    all_jobs = run_all_spiders()

    print(f"\n{'='*60}")
    print(f"========== {datetime.now()} 本轮抓取完成（共 {len(all_jobs)} 条） ==========")
    print(f"{'='*60}")


# ==========================================
# 定时调度配置
# ==========================================
USE_SCHEDULE = False  # 默认不启用内建定时，由 PM2 控制重启频率

if USE_SCHEDULE:
    import schedule

    # 每天 07:00 到 23:00，每两小时执行一次
    for hour in range(7, 24, 2):
        time_str = f"{hour:02d}:00"
        schedule.every().day.at(time_str).do(run_scrapers)

    print("调度器已启动（内建定时模式），等待执行任务...")
    run_scrapers()  # 立即执行一次

    while True:
        schedule.run_pending()
        time.sleep(60)
else:
    # PM2 托管模式：立即执行一次后退出，由 PM2 的 restart 策略控制频率
    run_scrapers()
