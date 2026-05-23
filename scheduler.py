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
import json
import logging
import subprocess
import os
import traceback
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
logger = logging.getLogger("scheduler")

PROJECT_DIR = Path(__file__).parent
BRIDGE_SCRIPT = PROJECT_DIR / "openclaw_bridge.py"

# ── 飞书告警配置（从 .env 加载） ──
from dotenv import load_dotenv
_env_path = PROJECT_DIR / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
# 爬虫告警接收会话 ID（默认发送给机器人自己，可在 .env 中配置 SCRAPER_ALARM_CHAT_ID）
SCRAPER_ALARM_CHAT_ID = os.getenv("SCRAPER_ALARM_CHAT_ID", "")


def _get_feishu_tenant_token() -> Optional[str]:
    """
    获取飞书 tenant_access_token，用于发送消息。
    返回 token 字符串，失败返回 None。
    """
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.warning("[Alarm] 飞书凭证未配置，无法发送告警")
        return None
    try:
        import requests
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("tenant_access_token", "")
        else:
            logger.error(f"[Alarm] 获取 tenant_token 失败: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        logger.error(f"[Alarm] 获取 tenant_token 异常: {e}")
        return None


def send_scraper_alarm(platform: str, error_msg: str) -> None:
    """
    向飞书发送爬虫任务异常告警消息。
    
    参数:
        platform: 招聘网站名称（如 "Boss直聘"、"猎聘"）
        error_msg: 捕获到的关键异常信息
    """
    if not SCRAPER_ALARM_CHAT_ID:
        logger.info("[Alarm] SCRAPER_ALARM_CHAT_ID 未配置，跳过飞书告警")
        return

    token = _get_feishu_tenant_token()
    if not token:
        logger.error("[Alarm] 无法获取飞书 token，告警发送失败")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 截断过长的错误信息
    if len(error_msg) > 500:
        error_msg = error_msg[:500] + "..."

    alert_text = (
        f"🚨 **InterviewOS | 爬虫任务运行异常告警**\n"
        f"🖥️ 目标平台：{platform}\n"
        f"⏱️ 发生时间：{now_str}\n"
        f"❌ 错误摘要：{error_msg}\n"
        f"🛠️ 排查建议：请检查该平台爬虫的凭证或防爬策略。"
    )

    try:
        import requests
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": SCRAPER_ALARM_CHAT_ID,
                "msg_type": "text",
                "content": json.dumps({"text": alert_text}, ensure_ascii=False),
            },
            timeout=10,
        )
        if resp.ok:
            logger.info(f"[Alarm] 爬虫告警已发送至飞书（平台: {platform}）")
        else:
            logger.error(f"[Alarm] 飞书告警发送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"[Alarm] 飞书告警发送异常: {e}")


def _run_script(script_name: str, label: str) -> bool:
    """
    运行单个爬虫脚本，实时输出到终端（不使用 2>&1 重定向）。
    捕获异常时自动触发飞书告警，但不影响其他平台。

    Args:
        script_name: 脚本文件名（如 "crawler_deepseek.py"）
        label: 日志标签（如 "DeepSeek"）

    Returns:
        True 表示成功，False 表示失败
    """
    script_path = PROJECT_DIR / script_name
    if not script_path.exists():
        error_msg = f"脚本文件不存在: {script_path}"
        logger.error(f"❌ [{label}] {error_msg}")
        send_scraper_alarm(label, error_msg)
        return False

    logger.info(f"{'='*60}")
    logger.info(f"🚀 [{label}] 启动 {script_name}...")
    logger.info(f"{'='*60}")

    try:
        # ★ 关键：使用 sys.executable 而非 conda run，避免子进程环境变量丢失
        #   scheduler.py 已被 job_env 下的 Python 解释器启动，
        #   sys.executable 自动指向当前环境的 Python 绝对路径。
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=False,  # 实时输出到终端
            text=True,
            cwd=str(PROJECT_DIR),
        )
        if result.returncode == 0:
            logger.info(f"✅ [{label}] 完成 (返回码: {result.returncode})")
            return True
        else:
            error_msg = f"脚本返回非零退出码: {result.returncode}"
            logger.error(f"❌ [{label}] {error_msg}")
            send_scraper_alarm(label, error_msg)
            return False
    except subprocess.TimeoutExpired:
        error_msg = "爬虫脚本执行超时（300s）"
        logger.error(f"❌ [{label}] {error_msg}")
        send_scraper_alarm(label, error_msg)
        return False
    except FileNotFoundError:
        error_msg = f"Python 解释器未找到: {sys.executable}"
        logger.error(f"❌ [{label}] {error_msg}")
        send_scraper_alarm(label, error_msg)
        return False
    except Exception as e:
        tb = traceback.format_exc()
        error_msg = f"{e}\n{tb[:300]}"
        logger.error(f"❌ [{label}] 异常: {e}")
        logger.debug(f"[{label}] 完整堆栈:\n{tb}")
        send_scraper_alarm(label, error_msg)
        return False


def run_all_spiders() -> None:
    """
    串行运行所有爬虫，按顺序执行。

    执行顺序：
      1. 三方平台：BOSS直聘、猎聘（使用 DrissionPage，独立浏览器）
      2. 官网 API 类：腾讯（requests，轻量）
      3. 官网 Playwright 类：字节、DeepSeek、小红书、月之暗面、智谱、MiniMax、阿里

    【容灾设计】
    每个爬虫调用均包裹在 try...except 中，确保单个平台崩溃不会阻断后续平台。
    异常时仅发送一次告警，然后 continue 继续执行下一个平台。
    """
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 启动全平台抓取")
    print(f"{'='*60}\n")

    # ── 定义爬虫执行计划 ──
    spider_plan = [
        # (脚本文件名, 平台标签)
        # 第一阶段：三方平台
        ("spider_boss.py", "BOSS直聘"),
        ("spider_liepin.py", "猎聘"),
        # 第二阶段：官网 API 类（轻量，无需浏览器）
        # ("crawler_tencent.py", "腾讯"),  # 腾讯平台暂时注释，爬虫不稳定
        # 第三阶段：官网 Playwright 类（需要浏览器）
        ("bytedance_visual_crawler.py", "字节跳动"),
        ("crawler_deepseek.py", "DeepSeek"),
        ("crawler_xiaohongshu.py", "小红书"),
        ("crawler_moonshot.py", "月之暗面"),
        ("crawler_zhipu.py", "智谱AI"),
        ("crawler_minimax.py", "MiniMax"),
        ("crawler_alibaba.py", "阿里巴巴"),
    ]


    # ── 阶段标签映射 ──
    phase_map = {
        "spider_boss.py": "📌 第一阶段：三方招聘平台",
        "spider_liepin.py": None,  # 同属第一阶段，不重复打印
        "crawler_tencent.py": "📌 第二阶段：官网 API 类爬虫",
        "bytedance_visual_crawler.py": "📌 第三阶段：官网 Playwright 爬虫",
    }

    success_count = 0
    fail_count = 0

    for script_name, label in spider_plan:
        # 打印阶段分隔
        phase_title = phase_map.get(script_name)
        if phase_title:
            logger.info(f"\n{phase_title}")

        # ★ 容灾核心：每个爬虫独立 try/except，崩溃不影响后续
        try:
            ok = _run_script(script_name, label)
            if ok:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            tb = traceback.format_exc()
            error_msg = f"{e}\n{tb[:300]}"
            logger.error(f"❌ [{label}] 未捕获的严重异常: {e}")
            logger.debug(f"[{label}] 完整堆栈:\n{tb}")
            # 仅发送一次告警，然后继续下一个平台
            send_scraper_alarm(label, f"[严重异常] {error_msg}")
            continue

    elapsed = time.time() - start_time
    total = success_count + fail_count
    print(f"\n{'='*60}")
    print(f"✅ 全平台抓取完成！耗时 {elapsed:.0f} 秒")
    print(f"   成功: {success_count}/{total} | 失败: {fail_count}/{total}")
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
            [sys.executable, str(BRIDGE_SCRIPT)],
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
