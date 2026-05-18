"""
scheduler.py — 全平台爬虫调度器（v2.0）

串行调用所有爬虫（三方平台 + 官网），将标准化 JobItem 统一合并后，
最后统一触发 openclaw_bridge.py 将增量数据推送到 Notion。

数据流：
  三方平台（BOSS直聘、猎聘）→ 各自独立 JSON
  官网（字节、DeepSeek、小红书、腾讯、智谱、MiniMax、月之暗面、阿里）
    → 统一写入 data/openclaw_jobs.json（增量合并）
  最后 → openclaw_bridge.py → Notion

用法:
  conda run -n job_env python scheduler.py          # 立即执行一轮全量同步
  conda run -n job_env python scheduler.py --no-bridge  # 仅抓取，不触发 Notion 同步
"""

import sys
import io
import time
import logging
import subprocess
import os
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")

PROJECT_DIR = Path(__file__).parent
BRIDGE_SCRIPT = PROJECT_DIR / "openclaw_bridge.py"


def _run_script(script_name: str, label: str) -> bool:
    """
    运行单个爬虫脚本，实时输出到终端（不使用 2>&1 重定向）。

    Args:
        script_name: 脚本文件名（如 "crawler_deepseek.py"）
        label: 日志标签（如 "DeepSeek"）

    Returns:
        True 表示成功，False 表示失败
    """
    script_path = PROJECT_DIR / script_name
    if not script_path.exists():
        logger.error(f"❌ 脚本不存在: {script_path}")
        return False

    logger.info(f"{'='*60}")
    logger.info(f"🚀 [{label}] 启动 {script_name}...")
    logger.info(f"{'='*60}")

    try:
        # ★ 关键：不使用 capture_output，让 print/logger 实时回显到终端
        result = subprocess.run(
            ["conda", "run", "-n", "job_env", "python", str(script_path)],
            capture_output=False,  # 实时输出到终端
            text=True,
            cwd=str(PROJECT_DIR),
        )
        if result.returncode == 0:
            logger.info(f"✅ [{label}] 完成 (返回码: {result.returncode})")
            return True
        else:
            logger.error(f"❌ [{label}] 失败 (返回码: {result.returncode})")
            return False
    except Exception as e:
        logger.error(f"❌ [{label}] 异常: {e}")
        return False


def run_all_spiders() -> None:
    """
    串行运行所有爬虫，按顺序执行。

    执行顺序：
      1. 三方平台：BOSS直聘、猎聘（使用 DrissionPage，独立浏览器）
      2. 官网 API 类：腾讯（requests，轻量）
      3. 官网 Playwright 类：字节、DeepSeek、小红书、月之暗面、智谱、MiniMax、阿里
    """
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 启动全平台抓取")
    print(f"{'='*60}\n")

    # ── 第一阶段：三方平台 ──
    logger.info("📌 第一阶段：三方招聘平台")
    _run_script("spider_boss.py", "BOSS直聘")
    _run_script("spider_liepin.py", "猎聘")

    # ── 第二阶段：官网 API 类（轻量，无需浏览器） ──
    logger.info("\n📌 第二阶段：官网 API 类爬虫")
    _run_script("crawler_tencent.py", "腾讯")

    # ── 第三阶段：官网 Playwright 类（需要浏览器） ──
    logger.info("\n📌 第三阶段：官网 Playwright 爬虫")
    _run_script("bytedance_visual_crawler.py", "字节跳动")
    _run_script("crawler_deepseek.py", "DeepSeek")
    _run_script("crawler_xiaohongshu.py", "小红书")
    _run_script("crawler_moonshot.py", "月之暗面")
    _run_script("crawler_zhipu.py", "智谱AI")
    _run_script("crawler_minimax.py", "MiniMax")
    _run_script("crawler_alibaba.py", "阿里巴巴")

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"✅ 全平台抓取完成！耗时 {elapsed:.0f} 秒")
    print(f"{'='*60}")


def run_bridge() -> None:
    """最后统一触发 openclaw_bridge.py 推送到 Notion"""
    logger.info(f"\n{'='*60}")
    logger.info(f"🔄 统一触发桥接脚本: openclaw_bridge.py")
    logger.info(f"{'='*60}")

    # ★ 同步前置检查：确保 data/openclaw_jobs.json 存在且大小正常
    output_file = PROJECT_DIR / "data" / "openclaw_jobs.json"
    if not output_file.exists():
        logger.error(f"❌ 文件不存在: {output_file}，跳过 Notion 写入")
        return
    file_size = output_file.stat().st_size
    if file_size < 1024:  # <1KB
        logger.error(f"❌ 文件大小异常 ({file_size} bytes < 1KB)，跳过 Notion 写入")
        return
    logger.info(f"✅ 前置检查通过: {output_file} ({file_size} bytes)")

    try:
        env = dict(os.environ)
        env["FORCE_UPDATE"] = "1"
        result = subprocess.run(
            ["conda", "run", "-n", "job_env", "python", str(BRIDGE_SCRIPT)],
            capture_output=False,  # 实时输出
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
        )
        if result.returncode == 0:
            logger.info(f"✅ 桥接脚本执行完成")
        else:
            logger.error(f"❌ 桥接脚本失败 (返回码: {result.returncode})")
    except Exception as e:
        logger.error(f"❌ 桥接脚本异常: {e}")


def run_scrapers(no_bridge: bool = False):
    """全量同步入口"""
    print(f"\n{'='*60}")
    print(f"📅 {datetime.now()} 启动本轮全平台抓取")
    print(f"{'='*60}")

    run_all_spiders()

    if not no_bridge:
        run_bridge()
    else:
        logger.info("跳过 Notion 桥接（--no-bridge 模式）")

    print(f"\n{'='*60}")
    print(f"📅 {datetime.now()} 本轮全量同步完成")
    print(f"{'='*60}")


# ==========================================
# 入口
# ==========================================
if __name__ == "__main__":
    no_bridge = "--no-bridge" in sys.argv
    run_scrapers(no_bridge=no_bridge)
