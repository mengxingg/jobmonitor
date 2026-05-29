"""
飞书 WebSocket 长连接网关 — InterviewOS 核心引擎指令接收器

功能：
  1. 通过 WebSocket 长连接监听飞书 im.message.receive_v1 事件
  2. 解析用户发送的纯文本指令
  3. 意图识别路由：菜单引导 / 9 平台爬虫 / 深度分析 / 简报 / 兜底
  4. ChatOps 爬虫：后台串行拉起 spider_*.py / crawler_*.py，防 OOM
  5. 异步执行 OpenClaw 深度背调 + Notion 读写 + 飞书通知
  6. 终端回显 + 飞书自动回复确认

使用方式：
  1. 在 .env 中配置 FEISHU_APP_ID / FEISHU_APP_SECRET / NOTION_API_KEY / NOTION_DATABASE_ID
  2. conda run -n job_env python feishu_gateway.py
"""

import os
import json
import logging
import re
import sys
import subprocess
import threading
import asyncio
import random
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests as http_requests

# ── 全局 DEBUG 日志（强制打印所有底层报错） ──────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("feishu_gateway")

# ── 飞书消息去重滑动窗口 ──────────────────────────────────
# 记录最近 1000 条已处理的 message_id，防止飞书"至少投递一次"重传机制导致重复执行
# maxlen 满时自动淘汰最老的 ID，无需手动清空
processed_message_ids: deque[str] = deque(maxlen=1000)

from notion_sync import replace_report_blocks, REPORT_ANCHOR_TEXT

from dotenv import load_dotenv
import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1, CreateMessageRequest, CreateMessageRequestBody

# ── 加载环境变量 ──────────────────────────────────────────
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
logger.info(f"📂 加载 .env 文件路径: {env_path}")
load_dotenv(env_path)

APP_ID = os.getenv("FEISHU_APP_ID", "YOUR_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "YOUR_APP_SECRET")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

# 强制校验凭证是否读取成功
logger.info(f"🔑 读取到的 APP_ID: {str(APP_ID)[:5]}***")
logger.info(f"🔑 读取到的 APP_SECRET 长度: {len(str(APP_SECRET))}")
logger.info(f"🔑 NOTION_DATABASE_ID: {str(NOTION_DATABASE_ID)[:8]}...")

# ── 项目路径常量 ──────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OPENCLAW_JOBS_PATH = os.path.join(PROJECT_ROOT, "data", "openclaw_jobs.json")
OPENCLAW_SKILL_DIR = os.path.expanduser("~/.openclaw/workspace/skills/job-monitor")
OPENCLAW_TARGETS_PATH = os.path.join(OPENCLAW_SKILL_DIR, "targets.json")
BRIDGE_SCRIPT = os.path.join(PROJECT_ROOT, "openclaw_bridge.py")

# ── ChatOps 爬虫调度（9 平台架构，串行防 OOM） ──
# 全量抓取顺序：严格串行，单脚本失败不阻断后续平台
SPIDER_FULL_PLAN: list[tuple[str, str]] = [
    ("spider_boss.py", "BOSS直聘"),
    ("spider_liepin.py", "猎聘"),
    ("bytedance_visual_crawler.py", "字节跳动"),
    ("crawler_deepseek.py", "DeepSeek"),
    ("crawler_xiaohongshu.py", "小红书"),
    ("crawler_moonshot.py", "月之暗面"),
    ("crawler_zhipu.py", "智谱"),
    ("crawler_minimax.py", "MiniMax"),
    ("crawler_alibaba.py", "阿里巴巴"),
]

# (脚本, 平台名, 主指令及别名) — 构建匹配表时按触发词长度降序
SPIDER_PLATFORM_ENTRIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("spider_boss.py", "BOSS直聘", ("抓取BOSS直聘", "抓取boss直聘", "抓取BOSS", "抓BOSS直聘")),
    ("spider_liepin.py", "猎聘", ("抓取猎聘", "抓猎聘")),
    ("bytedance_visual_crawler.py", "字节跳动", ("抓取字节跳动", "抓取字节", "抓字节跳动")),
    ("crawler_deepseek.py", "DeepSeek", ("抓取DeepSeek", "抓取deepseek", "抓DeepSeek")),
    ("crawler_xiaohongshu.py", "小红书", ("抓取小红书", "抓小红书")),
    ("crawler_moonshot.py", "月之暗面", ("抓取月之暗面", "抓取KIMI", "抓取kimi", "抓月之暗面")),
    ("crawler_zhipu.py", "智谱", ("抓取智谱", "抓智谱")),
    ("crawler_minimax.py", "MiniMax", ("抓取MiniMax", "抓取minimax", "抓MiniMax")),
    ("crawler_alibaba.py", "阿里巴巴", ("抓取阿里巴巴", "抓取阿里", "抓阿里巴巴")),
]

SPIDER_PLATFORM_RULES: list[tuple[str, str, str]] = []
for _script, _label, _triggers in SPIDER_PLATFORM_ENTRIES:
    for _trigger in _triggers:
        SPIDER_PLATFORM_RULES.append((_trigger, _script, _label))
SPIDER_PLATFORM_RULES.sort(key=lambda r: len(r[0]), reverse=True)

SPIDER_FULL_KEYWORDS = ("全面抓取", "更新所有岗位", "全量抓取", "抓取全部", "抓取所有")

# 飞书 ChatOps 底部快捷菜单 · 引导文案（同步回复，不启后台任务）
MENU_GUIDE_BACKTEST = (
    "💡 深度情报侦察系统已就绪。请直接输入：\n\n"
    "『帮我背调一下 [公司名称]』\n\n"
    "例如：帮我背调一下 字节跳动"
)
MENU_GUIDE_CRAWL_OFFICIAL = (
    "🕷️ 当前支持单点抓取的企业官网及 AI 独角兽如下，请直接输入对应指令：\n"
    "- 抓取字节跳动\n"
    "- 抓取DeepSeek\n"
    "- 抓取小红书\n"
    "- 抓取月之暗面\n"
    "- 抓取智谱\n"
    "- 抓取MiniMax\n"
    "- 抓取阿里巴巴"
)
MENU_GUIDE_TRIGGERS: dict[str, str] = {
    "背调指南": MENU_GUIDE_BACKTEST,
    "深度背调": MENU_GUIDE_BACKTEST,
    "抓取官网指南": MENU_GUIDE_CRAWL_OFFICIAL,
}

# 菜单固定文案 / 功能词，禁止当作公司名或误触发 OpenClaw
MENU_STATIC_LABELS = frozenset({
    "背调指南",
    "深度背调",
    "抓取官网指南",
    "今日简报",
    "全面抓取",
    "更新所有岗位",
})

# AI 资讯雷达 · 飞书底部子菜单暗号（与后台一字不差，共 6 条）
AI_HOT_MENU_COMMANDS = [
    "看今日日报",
    "看精选条目",
    "看本周动态",
    "模型发布",
    "产品发布",
    "行业动态",
]
AI_HOT_MENU_COMMANDS_SET = frozenset(AI_HOT_MENU_COMMANDS)

# 合并进菜单静态词表，避免误触背调/实体提取
MENU_STATIC_LABELS = MENU_STATIC_LABELS | AI_HOT_MENU_COMMANDS_SET

NODE_NEWS_CARD_API = os.getenv(
    "NODE_NEWS_CARD_API",
    "http://127.0.0.1:3001/internal/news-card",
)

ENTITY_BLOCKLIST = frozenset({
    "深度背调",
    "背调指南",
    "今日简报",
    "抓取官网",
    "抓取官网指南",
    "全面抓取",
    "更新所有",
    "岗位抓取",
    "情报侦察",
    "深度情报",
    "新岗位",
    "岗位简报",
    "高分岗位",
    *AI_HOT_MENU_COMMANDS,
})

# 全局爬虫互斥锁：同一时刻只允许一个抓取任务（单平台或全量）
_crawl_lock = threading.Lock()

# ── 飞书消息发送（供后台线程调用） ──────────────────────

# 孤立 UTF-16 代理项（常见于 AI 摘要里的残缺 emoji）会导致 json.dumps 崩溃
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _sanitize_unicode_str(value: str) -> str:
    if not isinstance(value, str):
        return value
    cleaned = _SURROGATE_RE.sub("\ufffd", value)
    return cleaned.encode("utf-8", "replace").decode("utf-8")


def _sanitize_for_feishu_json(value: Any) -> Any:
    """递归清理卡片/文本中的非法 Unicode，避免 surrogates not allowed。"""
    if isinstance(value, str):
        return _sanitize_unicode_str(value)
    if isinstance(value, dict):
        return {k: _sanitize_for_feishu_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_feishu_json(v) for v in value]
    return value


def _feishu_json_dumps(payload: Any) -> str:
    return json.dumps(_sanitize_for_feishu_json(payload), ensure_ascii=False)


def _send_feishu_message(chat_id: str, text: str) -> bool:
    """向指定会话发送飞书文本消息"""
    try:
        client = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .build()

        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("text") \
            .content(_feishu_json_dumps({"text": text})) \
            .build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        resp = client.im.v1.message.create(request)
        if not resp.success():
            logger.error(f"❌ 飞书消息发送失败: code={resp.code}, msg={resp.msg}")
            return False
        logger.info(f"📤 飞书消息已发送至 {chat_id}")
        return True

    except Exception as e:
        logger.error(f"❌ 飞书消息发送异常: {e}")
        return False


def _send_feishu_interactive_card(chat_id: str, card: dict) -> bool:
    """向指定会话发送飞书 interactive 消息卡片"""
    try:
        client = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .build()

        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(_feishu_json_dumps(card)) \
            .build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        resp = client.im.v1.message.create(request)
        if not resp.success():
            logger.error(f"❌ 飞书卡片发送失败: code={resp.code}, msg={resp.msg}")
            return False
        logger.info(f"📤 飞书卡片已发送至 {chat_id}")
        return True

    except Exception as e:
        logger.error(f"❌ 飞书卡片发送异常: {e}")
        return False


def try_forward_ai_hot_news_to_node(user_text: str, chat_id: str) -> bool:
    """AI 资讯雷达 · 最高优先级（背调/爬虫/简报之前）。命中则转发 Node 并阻断后续路由。"""
    normalized = user_text.strip()
    if normalized not in AI_HOT_MENU_COMMANDS:
        return False

    logger.info(f"✅ 命中新闻菜单指令: {normalized}，准备转发 Node.js")
    try:
        resp = http_requests.post(
            NODE_NEWS_CARD_API,
            json={"text": normalized},
            timeout=15,
        )
        if resp.status_code == 200:
            payload = _sanitize_for_feishu_json(
                json.loads(resp.content.decode("utf-8", errors="replace")),
            )
            if payload.get("error"):
                _send_feishu_message(chat_id, "资讯雷达暂时离线。")
                logger.error(f"Node.js 业务错误: {payload.get('error')}")
                return True

            sent_ok = False
            msg_type = payload.get("msg_type")
            if msg_type == "interactive" and payload.get("card"):
                sent_ok = _send_feishu_interactive_card(chat_id, payload["card"])
            elif msg_type == "text":
                content = payload.get("content") or {}
                sent_ok = _send_feishu_message(
                    chat_id,
                    content.get("text", "资讯雷达暂时离线。"),
                )
            else:
                _send_feishu_message(chat_id, "资讯雷达暂时离线。")
                logger.error("Node.js 返回格式无法识别")
                return True

            if sent_ok:
                logger.info("✅ 新闻卡片已推送飞书")
            else:
                _send_feishu_message(chat_id, "资讯卡片发送失败，请稍后重试。")
            return True

        logger.error(f"Node.js 返回错误: {resp.status_code} {resp.text[:200]}")
        _send_feishu_message(chat_id, "资讯雷达暂时离线。")
        return True

    except Exception as e:
        logger.error(f"连接 Node.js 失败: {e}")
        _send_feishu_message(
            chat_id,
            "资讯雷达暂时离线。请确认已执行：npm run feishu-local-api",
        )
        return True


# ── 实体提取 ──────────────────────────────────────────────


def extract_company_name(text: str, *, allow_fallback: bool = True) -> Optional[str]:
    """
    从用户自然语言指令中提取目标公司名称。
    例如："帮我背调一下字节跳动" → "字节跳动"
          "分析一下阿里巴巴的岗位" → "阿里巴巴"
    """
    normalized = text.strip()
    if not normalized or normalized in MENU_STATIC_LABELS:
        logger.warning("[Entity] 菜单固定文案，跳过公司名提取")
        return None

    # 优先从 targets.json 中匹配已知公司名
    try:
        with open(OPENCLAW_TARGETS_PATH, "r", encoding="utf-8") as f:
            targets_data = json.load(f)
        known_companies = [t["company"] for t in targets_data.get("targets", [])]
    except Exception:
        known_companies = []

    # 按长度降序排列（优先匹配长名称，避免"百度"被"百川智能"的"百"提前匹配）
    known_companies.sort(key=len, reverse=True)

    for company in known_companies:
        if company in normalized:
            logger.info(f"[Entity] 从指令中提取到公司名: {company}")
            return company

    if not allow_fallback:
        logger.warning("[Entity] 未匹配已知公司（未启用兜底提取）")
        return None

    # 兜底：尝试提取任意中文公司名（2-6 个中文字符），排除菜单/功能词
    for fallback_match in re.finditer(r"([\u4e00-\u9fa5]{2,8})", normalized):
        candidate = fallback_match.group(1)
        if candidate in ENTITY_BLOCKLIST:
            continue
        if any(blocked in candidate for blocked in ("背调", "简报", "抓取", "指南")):
            continue
        logger.info(f"[Entity] 未匹配已知公司，使用兜底提取: {candidate}")
        return candidate

    logger.warning("[Entity] 无法从指令中提取公司名")
    return None


def is_research_command(text: str) -> bool:
    """
    判断是否为「带公司名的深度背调」自然语言指令。
    菜单项「深度背调」「背调指南」等固定文案不命中。
    """
    normalized = text.strip()
    if not normalized or normalized in MENU_STATIC_LABELS:
        return False
    if normalized in MENU_GUIDE_TRIGGERS:
        return False

    strong_patterns = (
        "帮我背调",
        "背调一下",
        "背调下",
        "请背调",
        "帮我分析",
        "分析一下",
        "分析下",
        "深度分析",
        "研究一下",
    )
    if any(p in normalized for p in strong_patterns):
        return True

    if any(kw in normalized for kw in ("分析", "研究", "看看")):
        return extract_company_name(normalized, allow_fallback=False) is not None

    if "背调" in normalized:
        return extract_company_name(normalized, allow_fallback=False) is not None

    return False


def _parse_notion_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """解析 Notion ISO8601 时间戳为 UTC aware datetime。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_discovered_date_from_props(props: dict) -> Optional[datetime]:
    """从 Notion 页面属性读取 Discovered Date。"""
    date_field = props.get("Discovered Date", {}).get("date") or {}
    start = date_field.get("start")
    if not start:
        return None
    try:
        if "T" in start:
            return datetime.fromisoformat(start.replace("Z", "+00:00"))
        return datetime.fromisoformat(start + "T00:00:00+00:00")
    except ValueError:
        return None


def _is_job_within_24h(page: dict, cutoff_utc: datetime) -> bool:
    """
    岗位是否属于过去 24 小时内：
      - 以 Notion 页面 created_time（入库时间）为准（主条件）
      - 若存在 Discovered Date 且早于 cutoff，则排除（避免 5/22 旧帖）
    """
    created = _parse_notion_timestamp(page.get("created_time"))
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created < cutoff_utc:
        return False

    props = page.get("properties", {})
    discovered = _parse_discovered_date_from_props(props)
    if discovered is not None:
        if discovered.tzinfo is None:
            discovered = discovered.replace(tzinfo=timezone.utc)
        if discovered < cutoff_utc:
            return False

    return True


# ── Notion 查询 ───────────────────────────────────────────


def query_notion_by_company(company: str) -> list[dict]:
    """
    在 Notion 数据库中查询指定公司的最新岗位记录。
    返回岗位列表（含 title, url, jd_summary 等字段）。
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.error("[Notion] API 密钥或数据库 ID 未配置")
        return []

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    filter_body = {
        "filter": {
            "property": "Company",
            "rich_text": {"contains": company},
        },
        "sorts": [{"property": "Discovered Date", "direction": "descending"}],
        "page_size": 10,
    }

    try:
        import requests
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers,
            json=filter_body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        logger.info(f"[Notion] 查询公司 '{company}' 返回 {len(results)} 条记录")

        jobs = []
        for page in results:
            props = page.get("properties", {})
            title_field = props.get("Title", {}).get("title", [])
            title = title_field[0].get("text", {}).get("content", "") if title_field else ""
            url_field = props.get("URL", {})
            url = url_field.get("url") or (
                url_field.get("rich_text", [{}])[0].get("text", {}).get("content", "")
                if url_field.get("rich_text") else ""
            )
            company_field = props.get("Company", {}).get("rich_text", [])
            company_name = company_field[0].get("text", {}).get("content", "") if company_field else ""
            jd_summary_field = props.get("JD Summary", {}).get("rich_text", [])
            jd_summary = jd_summary_field[0].get("text", {}).get("content", "") if jd_summary_field else ""

            jobs.append({
                "page_id": page["id"],
                "title": title,
                "company": company_name,
                "url": url,
                "jd_summary": jd_summary,
            })

        return jobs

    except Exception as e:
        logger.error(f"[Notion] 查询失败: {e}", exc_info=True)
        return []


def query_notion_recent_24h() -> list[dict]:
    """
    查询 Notion 数据库中过去 24 小时内新增的岗位记录（不限匹配分数）。
    筛选条件：
      - 页面 created_time（入库时间）在过去 24 小时内
      - Discovered Date 不得早于 cutoff（排除历史帖被误收录）
    按入库时间降序排列。

    返回:
        岗位列表，每项包含 title, company, score, url, location, salary, page_id,
        discovered_date, notion_created_time
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.error("[Notion] API 密钥或数据库 ID 未配置")
        return []

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=24)

    filter_body = {
        "filter": {
            "timestamp": "created_time",
            "created_time": {"after": cutoff_utc.isoformat()},
        },
        "sorts": [
            {"timestamp": "created_time", "direction": "descending"},
        ],
        "page_size": 50,
    }

    try:
        import requests
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers,
            json=filter_body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        logger.info(f"[Notion] API 初筛返回 {len(results)} 条（created_time 过去 24h，不限分数）")

        jobs = []
        for page in results:
            if not _is_job_within_24h(page, cutoff_utc):
                props = page.get("properties", {})
                disc = _parse_discovered_date_from_props(props)
                created = _parse_notion_timestamp(page.get("created_time"))
                logger.debug(
                    f"[Notion] 二次过滤剔除: created={created}, discovered={disc}"
                )
                continue

            props = page.get("properties", {})
            title_field = props.get("Title", {}).get("title", [])
            title = title_field[0].get("text", {}).get("content", "") if title_field else ""
            company_field = props.get("Company", {}).get("rich_text", [])
            company = company_field[0].get("text", {}).get("content", "") if company_field else ""
            score = props.get("Match Score", {}).get("number", 0) or 0
            url_field = props.get("URL", {})
            url = url_field.get("url") or ""
            location_field = props.get("Location", {}).get("rich_text", [])
            location = location_field[0].get("text", {}).get("content", "") if location_field else ""
            salary_field = props.get("Salary Range", {}).get("rich_text", [])
            salary = salary_field[0].get("text", {}).get("content", "") if salary_field else ""

            discovered_dt = _parse_discovered_date_from_props(props)
            discovered_str = (
                discovered_dt.astimezone().strftime("%Y-%m-%d")
                if discovered_dt
                else "未知"
            )
            created_dt = _parse_notion_timestamp(page.get("created_time"))
            created_str = (
                created_dt.astimezone().strftime("%Y-%m-%d %H:%M")
                if created_dt
                else "未知"
            )

            jobs.append({
                "page_id": page["id"],
                "title": title,
                "company": company,
                "score": score,
                "url": url,
                "location": location,
                "salary": salary,
                "discovered_date": discovered_str,
                "notion_created_time": created_str,
            })

        logger.info(f"[Notion] 过去 24 小时最终保留 {len(jobs)} 条岗位")
        return jobs[:20]

    except Exception as e:
        logger.error(f"[Notion] 24h 查询失败: {e}", exc_info=True)
        return []


# ── OpenClaw 调用 ─────────────────────────────────────────


def run_openclaw_crawl(company: str) -> bool:
    """
    调用 OpenClaw CLI 对指定公司进行深度抓取。
    输出保存到 data/openclaw_jobs.json。

    返回 True 表示成功，False 表示失败。
    """
    logger.info(f"[OpenClaw] 开始对 '{company}' 执行深度抓取...")

    # 构造 OpenClaw 命令
    # 使用 openclaw run skill 语法，传入公司名作为参数
    cmd = [
        "openclaw", "run", "skill", "job-monitor",
        "--target", company,
        "--output", OPENCLAW_JOBS_PATH,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            logger.info(f"[OpenClaw] 抓取成功，输出已保存到 {OPENCLAW_JOBS_PATH}")
            logger.debug(f"[OpenClaw] stdout: {result.stdout[-500:]}")
            return True
        else:
            logger.error(f"[OpenClaw] 抓取失败，returncode={result.returncode}")
            logger.error(f"[OpenClaw] stderr: {result.stderr[-500:]}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"[OpenClaw] 抓取超时（5分钟）")
        return False
    except FileNotFoundError:
        logger.error("[OpenClaw] openclaw 命令未找到，请确认已安装 OpenClaw CLI")
        return False
    except Exception as e:
        logger.error(f"[OpenClaw] 抓取异常: {e}", exc_info=True)
        return False


# ── 降级自愈矩阵 ──────────────────────────────────────────
# 用于追踪本次背调中触发的降级事件，最终汇总为飞书告警卡片
HEALING_EVENTS: list[dict] = []


def _reset_healing_events():
    """清空降级事件列表（每次背调开始前调用）"""
    HEALING_EVENTS.clear()


def _record_healing_event(target: str, status: str, action: str):
    """
    记录一条降级自愈事件。
    
    参数:
        target: 目标描述（如"官网首页"、"技术博客"）
        status: 状态描述（如"抓取失败 (状态码 403)"）
        action: 自愈动作描述（如"已自动降级至主流新闻检索补盲"）
    """
    event = {
        "target": target,
        "status": status,
        "action": action,
    }
    HEALING_EVENTS.append(event)
    logger.warning(f"[Healing] 记录降级事件: target={target}, status={status}, action={action}")


def _send_healing_alert(chat_id: str, company: str, final_state: str):
    """
    向飞书推送巡检自愈告警卡片。
    
    参数:
        chat_id: 飞书会话 ID
        company: 公司名称
        final_state: 最终状态描述（如"已基于现有实时情报生成折损版报告，并成功写入 Notion"）
    """
    if not HEALING_EVENTS:
        return  # 没有降级事件，不推送告警

    lines = [f"⚠️ **OpenClaw 巡检告警：[{company}] 深度抓取触发自愈**"]
    for evt in HEALING_EVENTS:
        lines.append(f"- ❌ {evt['target']}：{evt['status']}")
        lines.append(f"- 🔄 自愈动作：{evt['action']}")
    lines.append(f"- 📊 最终状态：{final_state}")

    alert_text = "\n".join(lines)
    _send_feishu_message(chat_id, alert_text)
    logger.info(f"[Healing] 巡检告警已推送至 {chat_id}（公司: {company}, 事件数: {len(HEALING_EVENTS)}）")


# ── 备用情报源（降级 B 方案） ────────────────────────────
# 当 OpenClaw web_fetch 失败时，直接通过 requests 抓取主流科技媒体新闻快照
# 这些站点抗反爬能力较强，作为低成本备用方案


FALLBACK_NEWS_SOURCES = [
    "https://www.jiqizhixin.com/search?keyword={company}+AI",
    "https://www.leiphone.com/search?q={company}+AI",
    "https://www.36kr.com/search/articles/{company}",
]


def _fallback_fetch_news(company: str) -> Optional[str]:
    """
    备用方案 B：通过 requests 直接抓取主流科技媒体的新闻快照。
    使用随机 User-Agent 和超时控制，作为 OpenClaw web_fetch 失败后的降级。
    
    返回抓取到的文本片段，失败返回 None。
    """
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    ]

    import requests as http_req

    for source_url in FALLBACK_NEWS_SOURCES:
        url = source_url.format(company=http_req.utils.quote(company) if hasattr(http_req.utils, 'quote') else company)
        ua = random.choice(user_agents)
        try:
            resp = http_req.get(
                url,
                headers={"User-Agent": ua},
                timeout=15,
            )
            if resp.ok:
                text = resp.text[:3000]
                # 简单提取可见文本（去除 HTML 标签）
                import re as _re
                text = _re.sub(r'<[^>]+>', '', text)
                text = _re.sub(r'\s+', ' ', text).strip()
                if len(text) > 100:
                    logger.info(f"[Fallback] 备用新闻源抓取成功: {url} ({len(text)} 字符)")
                    return f"【备用新闻源】{url}\n{text[:2000]}"
                else:
                    logger.warning(f"[Fallback] 备用新闻源返回内容过短: {url}")
            else:
                logger.warning(f"[Fallback] 备用新闻源返回 {resp.status_code}: {url}")
        except Exception as e:
            logger.warning(f"[Fallback] 备用新闻源异常: {url} -> {e}")

    return None


def run_openclaw_web_research(company: str) -> Optional[str]:
    """
    调用 OpenClaw 的 web_fetch 能力，对指定公司进行外部情报深挖。
    抓取该公司近期的官方新闻、技术博客或创始人关于 AI 的访谈。

    强制绑定 job-insight 技能，防止大模型幻觉：
      - 必须严格调用并遵守 job-insight 技能的所有规则
      - 绝对禁止使用预训练记忆，必须使用 web_search 和 web_fetch 获取实时来源
      - 如果没有网页来源标注，任务直接失败

    ★ 降级自愈矩阵：
      机制 A：自动更换 User-Agent 并重试一次
      机制 B（核心降级）：若依然失败，自动切换为调用备用方案（requests 直抓科技媒体新闻快照）

    返回:
        抓取到的外部情报文本（原始内容），可能为 None
    """
    logger.info(f"[OpenClaw-Research] 开始对 '{company}' 执行外部情报深挖...")

    # 构造多个搜索/抓取目标，覆盖不同信息源
    search_queries = [
        f"{company} AI 战略 2026",
        f"{company} 创始人 AI 访谈",
        f"{company} 技术博客 AI",
        f"{company} 大模型 产品",
    ]

    # ════════════════════════════════════════════════════════════
    # 强制绑定 job-insight 技能的前缀指令（极端强硬，防偷懒）
    # 该前缀会注入到 OpenClaw 的 prompt 中，约束其行为
    # ════════════════════════════════════════════════════════════
    JOB_INSIGHT_PREFIX = (
        "【最高指令】：你的内置知识库已被清空！面对这家公司，你处于完全无知的状态。"
        "你**必须、绝对、一定要**调用 web_search 工具搜索该公司近3个月的动态，"
        "并使用 web_fetch 阅读网页。报告中的任何结论必须附带类似 (来源：https://...) 的网址！"
        "如果报告中出现'推测'、'推断'字眼，或缺少真实的 http URL 来源，"
        "你的任务将直接被判定为严重失败！"
        "请严格使用 job-insight 技能执行。"
        f"请对【{company}】进行背调。"
    )

    # ── User-Agent 轮换池（机制 A 用） ──
    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ]

    all_insights = []
    openclaw_failed_queries = 0
    total_queries = len(search_queries)

    for query in search_queries:
        success = False
        # ── 机制 A：首次尝试 ──
        for attempt in range(1, 3):  # 最多 2 次（首次 + 更换 UA 重试）
            try:
                ua = random.choice(USER_AGENTS)
                cmd = [
                    "openclaw", "run", "skill", "job-insight",
                    "--target", company,
                    "--query", f"{JOB_INSIGHT_PREFIX} {query}",
                    "--output", OPENCLAW_JOBS_PATH,
                ]
                # 通过环境变量传递 User-Agent（如果 OpenClaw 支持）
                env = os.environ.copy()
                env["HTTP_USER_AGENT"] = ua

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=PROJECT_ROOT,
                    env=env,
                )
                if result.returncode == 0:
                    all_insights.append(f"【搜索: {query}】\n{result.stdout[:2000]}")
                    logger.info(f"[OpenClaw-Research] 查询 '{query}' 成功（attempt={attempt}）")
                    success = True
                    break
                else:
                    # 检查是否因 403/超时 等可自愈原因失败
                    stderr_lower = result.stderr.lower()
                    is_retryable = any(kw in stderr_lower for kw in [
                        "403", "timeout", "timed out", "connection refused",
                        "captcha", "blocked", "rate limit",
                    ])
                    if is_retryable and attempt == 1:
                        logger.warning(f"[OpenClaw-Research] 查询 '{query}' 触发可自愈错误 (attempt={attempt})，更换 UA 重试...")
                        _record_healing_event(
                            target=f"OpenClaw 查询: {query[:30]}...",
                            status=f"抓取失败 (attempt={attempt})",
                            action="机制 A：已自动更换 User-Agent 重试",
                        )
                        time.sleep(2)  # 短暂等待后重试
                    else:
                        logger.warning(f"[OpenClaw-Research] 查询 '{query}' 返回非零 (attempt={attempt}): {result.returncode}")
                        openclaw_failed_queries += 1
            except subprocess.TimeoutExpired:
                logger.warning(f"[OpenClaw-Research] 查询 '{query}' 超时 (attempt={attempt})")
                if attempt == 1:
                    _record_healing_event(
                        target=f"OpenClaw 查询: {query[:30]}...",
                        status="抓取超时 (300s)",
                        action="机制 A：已自动更换 User-Agent 重试",
                    )
                    openclaw_failed_queries += 1  # 重试也超时的话算失败
                else:
                    openclaw_failed_queries += 1
            except Exception as e:
                logger.warning(f"[OpenClaw-Research] 查询 '{query}' 异常 (attempt={attempt}): {e}")
                if attempt == 2:  # 两次都失败
                    openclaw_failed_queries += 1

        if not success:
            openclaw_failed_queries += 1

    # ── 机制 B（核心降级）：如果 OpenClaw 全部失败，降级到备用新闻源 ──
    if not all_insights:
        logger.info("[OpenClaw-Research] OpenClaw 搜索全部失败，触发机制 B 降级...")
        _record_healing_event(
            target="OpenClaw web_fetch 全部查询",
            status=f"共 {total_queries} 条查询全部失败",
            action="机制 B：已自动降级至主流科技媒体新闻快照检索",
        )

        # 尝试备用新闻源
        fallback_text = _fallback_fetch_news(company)
        if fallback_text:
            all_insights.append(fallback_text)
            logger.info("[OpenClaw-Research] 备用新闻源降级成功")
        else:
            logger.warning("[OpenClaw-Research] 备用新闻源也全部失败")

        # 从 targets.json 获取该公司 URL 作为最后兜底
        try:
            with open(OPENCLAW_TARGETS_PATH, "r", encoding="utf-8") as f:
                targets_data = json.load(f)
            for t in targets_data.get("targets", []):
                if t["company"] == company:
                    company_url = t.get("url", "")
                    if company_url:
                        all_insights.append(f"【官网/招聘页】\n{company_url}")
                        _record_healing_event(
                            target="官网/招聘页 URL",
                            status="从 targets.json 获取",
                            action="已提取 URL 作为情报线索",
                        )
                    break
        except Exception as e:
            logger.warning(f"[OpenClaw-Research] 读取 targets.json 失败: {e}")

    combined = "\n\n---\n\n".join(all_insights) if all_insights else None
    if combined:
        logger.info(f"[OpenClaw-Research] 外部情报抓取完成，总长度: {len(combined)} 字符")
    else:
        logger.warning("[OpenClaw-Research] 未抓取到任何外部情报")
    return combined


def generate_deep_research_report(company: str, jobs: list[dict], external_insights: Optional[str]) -> tuple[str, str, str]:
    """
    结合 Notion 岗位 JD + 外部情报，调用 AI 生成深度背调报告。

    返回:
        (full_report_markdown, core_summary_line1, core_summary_line2)
        - full_report_markdown: 完整的 Markdown 深度报告（写入 Notion）
        - core_summary_line1: 核心情报1（一句话，20字以内）
        - core_summary_line2: 核心情报2（一句话，20字以内）
    """
    # 构建岗位信息文本
    jobs_text = ""
    if jobs:
        for i, job in enumerate(jobs, 1):
            jobs_text += f"""
### {i}. {job.get('title', '未知岗位')}
- **URL**: {job.get('url', '无')}
- **JD 摘要**: {job.get('jd_summary', '无')[:300]}
"""
    else:
        jobs_text = "（Notion 中暂无该公司的岗位记录）"

    # 构建外部情报文本
    external_text = external_insights if external_insights else "（未获取到外部情报）"

    prompt = f"""你是一位专业的 AI 招聘分析师与行业情报研究员。请对以下 {company} 进行深度背调分析。

## 📋 任务说明
请结合「外部情报」与「岗位 JD」，生成一份深度背调报告。

## 🌐 外部情报（近期新闻/技术博客/创始人访谈）
{external_text}

## 💼 当前在招 AI 产品经理岗位
{jobs_text}

## 📊 分析要求
请从以下维度进行分析：

### 一、公司 AI 战略全景
- 该公司当前 AI 战略重点是什么？
- 近期发布了哪些重要产品/技术？
- 创始人对 AI 行业的最新判断

### 二、岗位画像分析
- 在招的 AI PM 岗位类型分布
- 共同的技能要求和技术栈
- 薪资水平评估

### 三、团队与文化推断
- 从 JD 细节推断团队阶段和文化
- 组织架构位置（属于哪个部门）

### 四、候选人匹配建议
- 对该岗位候选人的具体建议
- 面试准备方向

请用 Markdown 格式输出完整的分析报告。"""

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一位专业的 AI 招聘分析师与行业情报研究员。请用中文输出 Markdown 格式的分析报告。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=3000,
            timeout=90,
        )

        full_report = resp.choices[0].message.content
        logger.info(f"[AI] 深度背调报告生成完成，长度: {len(full_report)} 字符")

        # 提取核心情报摘要（用于飞书卡片）
        summary_prompt = f"""根据以下深度背调报告，提取两条核心情报，每条不超过20个字：

1. 📌 该公司近期 AI 战略重点（一句话概括）
2. 📌 该岗位最看重的技术栈/能力（一句话概括）

报告内容：
{full_report[:2000]}

请严格按以下格式输出，不要有多余内容：
战略重点：xxx
核心能力：xxx"""

        summary_resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个信息提取助手。请严格按指定格式输出。"},
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.3,
            max_tokens=100,
            timeout=30,
        )

        summary_text = summary_resp.choices[0].message.content
        # 解析摘要
        line1, line2 = "", ""
        for line in summary_text.strip().split("\n"):
            if "战略重点" in line:
                line1 = line.split("：")[-1].strip() if "：" in line else line
            elif "核心能力" in line:
                line2 = line.split("：")[-1].strip() if "：" in line else line

        if not line1:
            line1 = f"{company} AI 战略布局"
        if not line2:
            line2 = "AI 产品经理综合能力"

        return full_report, line1, line2

    except Exception as e:
        logger.error(f"[AI] 深度背调报告生成失败: {e}", exc_info=True)
        fallback_report = f"## 🔍 {company} 深度背调报告\n\nAI 分析生成失败: {e}"
        return fallback_report, f"{company} AI 战略布局", "AI 产品经理综合能力"


def generate_briefing_report(jobs: list[dict]) -> str:
    """
    对过去 24 小时的岗位列表，调用 AI 生成极客风格早报卡片。
    
    返回严格对齐格式的早报文本，包含：
      - 标题 + 统计周期 + 岗位数量
      - 每个岗位：公司名、岗位名、薪资、地点、核心匹配点、Notion 链接
      - 底部提示语
    """
    if not jobs:
        return ""

    # 构建岗位信息文本（供 AI 提炼）
    jobs_text = ""
    for i, job in enumerate(jobs, 1):
        jobs_text += (
            f"{i}. **{job.get('title', '未知')}** @ {job.get('company', '未知')}\n"
            f"   薪资: {job.get('salary', '面议')} | 地点: {job.get('location', '未知')}\n"
            f"   匹配度: {job.get('score', 'N/A')} | "
            f"发现日: {job.get('discovered_date', '未知')} | "
            f"入库: {job.get('notion_created_time', '未知')}\n"
        )

    prompt = f"""你是一位 AI 招聘简报编辑。以下是过去 24 小时内新增的 AI 产品经理岗位列表（不限匹配分数）。

## 岗位列表
{jobs_text}

## 任务
请为每个岗位生成一条"核心匹配点"（一句话，15 字以内），说明为什么这个岗位适合候选人。

## 输出格式要求
请严格按以下格式输出，每行一个岗位，不要有多余内容：

公司A | 岗位A | 匹配点一句话
公司B | 岗位B | 匹配点一句话
..."""

    match_points: list[str] = []
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个信息提取助手。请严格按指定格式输出。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
            timeout=30,
        )

        output = resp.choices[0].message.content
        for line in output.strip().split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                match_points.append(parts[2])
            else:
                match_points.append("AI 产品经理方向")
        logger.info(f"[Briefing] AI 匹配点提炼完成，共 {len(match_points)} 条")

    except Exception as e:
        logger.warning(f"[Briefing] AI 匹配点提炼失败: {e}，使用默认值")
        match_points = ["AI 产品经理方向"] * len(jobs)

    # 补齐匹配点数量
    while len(match_points) < len(jobs):
        match_points.append("AI 产品经理方向")

    # ── 组装极客风格早报卡片 ──
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"☀️ **InterviewOS | 今日岗位简报**",
        f"📅 统计周期：过去 24 小时（按 Notion 入库时间）",
        f"📈 今日新收录岗位：{len(jobs)} 个",
        f"",
        f"━━━━━━━━━━━━━━━━━━",
    ]

    for i, job in enumerate(jobs):
        company = job.get("company", "未知公司")
        title = job.get("title", "未知岗位")
        salary = job.get("salary", "面议")
        location = job.get("location", "未知")
        match_pt = match_points[i] if i < len(match_points) else "AI 产品经理方向"
        page_id = job.get("page_id", "")
        notion_link = f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else ""

        lines.append(f"🏢 **{company} · {title}**")
        lines.append(f"💰 薪资：{salary} | 📍 地点：{location}")
        lines.append(
            f"📅 发现日：{job.get('discovered_date', '未知')} | "
            f"入库：{job.get('notion_created_time', '未知')}"
        )
        lines.append(f"🎯 核心匹配点：{match_pt}")
        if notion_link:
            lines.append(f"🔗 [查看详情]({notion_link})")
        lines.append("")  # 空行分隔

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("💡 *提示：你可以直接在飞书对我说\"帮我背调一下[公司名]\"，唤醒 OpenClaw 执行深度情报深挖。*")

    return "\n".join(lines)


# ── 后台任务执行（非阻塞） ──────────────────────────────


def _background_research(text: str, chat_id: str) -> None:
    """
    后台执行深度背调全流程（在独立线程中运行，不阻塞 WebSocket 主线程）：
      1. 提取公司名
      2. 查询 Notion 获取该公司岗位
      3. 调用 OpenClaw 外部情报深挖（web_fetch，强制绑定 job-insight 技能）
      4. AI 结合外部情报 + 岗位 JD 生成深度背调报告
      5. 完整报告通过 Notion block children API 写入页面内容（不受 2000 字符限制）
         Notes 字段仅写入一句话摘要，不重复粘贴 JD
      6. 飞书仅推送极简摘要卡片
    """
    try:
        # 0. 重置降级事件列表（每次背调开始前清空）
        _reset_healing_events()

        # 1. 提取公司名
        company = extract_company_name(text)
        if not company:
            _send_feishu_message(chat_id, "❌ 无法从指令中识别目标公司，请明确说出公司名称（如'帮我背调一下字节跳动'）")
            return

        _send_feishu_message(chat_id, f"🔍 已识别目标公司：**{company}**\n⏳ 正在查询 Notion 数据库中的岗位记录...")

        # 2. 查询 Notion 获取该公司岗位
        jobs = query_notion_by_company(company)
        logger.info(f"[Research] Notion 查询到 {len(jobs)} 条 {company} 的岗位记录")

        # 3. 调用 OpenClaw 外部情报深挖（web_fetch，强制绑定 job-insight 技能）
        _send_feishu_message(chat_id, f"⏳ 正在通过 OpenClaw 对 {company} 进行外部情报深挖（新闻/技术博客/创始人访谈）...")
        external_insights = run_openclaw_web_research(company)
        if external_insights:
            logger.info(f"[Research] 外部情报抓取成功，长度: {len(external_insights)} 字符")
        else:
            logger.warning("[Research] 未获取到外部情报，将仅基于岗位 JD 进行分析")

        # 4. AI 结合外部情报 + 岗位 JD 生成深度背调报告
        _send_feishu_message(chat_id, f"⏳ 正在调用 AI 生成深度背调报告（结合外部情报 + 岗位 JD）...")
        full_report, summary_line1, summary_line2 = generate_deep_research_report(company, jobs, external_insights)

        # 5. 提取最新岗位的 Title（用于飞书卡片告知用户报告挂载路径）
        target_title = jobs[0].get("title", "最新岗位") if jobs else "最新岗位"
        target_page_id = jobs[0].get("page_id") if jobs else None

        # 6. 完整报告写入 Notion（使用 block children API，不受 2000 字符限制）
        #    Notes 字段仅写入一句话摘要，不重复粘贴 JD
        notion_write_ok = False
        if target_page_id:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {NOTION_API_KEY}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                }

                # 6a. Notes 字段仅写入一句话摘要
                notes_summary = f"深度背调已完成，详见页面内容。核心：{summary_line1}；{summary_line2}"
                update_body = {
                    "properties": {
                        "Notes": {
                            "rich_text": [{"text": {"content": notes_summary[:200]}}]
                        }
                    }
                }
                resp = requests.patch(
                    f"https://api.notion.com/v1/pages/{target_page_id}",
                    headers=headers,
                    json=update_body,
                    timeout=15,
                )
                if resp.ok:
                    logger.info(f"[Notion] Notes 摘要已写入 page {target_page_id}（岗位: {target_title}）")
                else:
                    logger.warning(f"[Notion] Notes 写入失败: {resp.status_code}")

                # 6b. ★ 精准替换报告逻辑（锚点驱动） ★
                #     1. 在 Markdown 报告顶部强制插入锚点标记
                #     2. 将 Markdown 转换为 Notion blocks
                #     3. 调用 replace_report_blocks 执行"扫描→删除→追加"原子操作
                anchored_report = f"## {REPORT_ANCHOR_TEXT}\n\n{full_report}"
                report_blocks = _markdown_to_notion_blocks(anchored_report)
                if report_blocks:
                    notion_write_ok = replace_report_blocks(target_page_id, report_blocks)
                    if notion_write_ok:
                        logger.info(f"[Notion] 深度背调报告已通过 replace_report_blocks 精准替换写入 page {target_page_id}（岗位: {target_title}），共 {len(report_blocks)} 个 block")
                    else:
                        logger.warning(f"[Notion] replace_report_blocks 写入失败")
                else:
                    logger.warning("[Notion] 报告为空，跳过 block children 写入")

            except Exception as e:
                logger.error(f"[Notion] 写入分析报告异常: {e}", exc_info=True)

        # 7. 飞书仅推送极简摘要卡片（禁止推送长篇 Markdown）
        #    明确告知用户报告挂载在哪个岗位下
        notion_link = f"https://www.notion.so/{target_page_id.replace('-', '')}" if target_page_id else "Notion 数据库"
        feishu_card = (
            f"✅ **{company} 深度背调已完成**\n"
            f"📁 报告已挂载至最新岗位：**{target_title}** 页面下\n"
            f"💡 核心情报提取：\n"
            f"- 📌 {summary_line1}\n"
            f"- 📌 {summary_line2}\n"
            f"🔗 [点击此处直达 Notion 查看]({notion_link})"
        )
        _send_feishu_message(chat_id, feishu_card)
        logger.info(f"[Research] 飞书极简摘要卡片已推送至 {chat_id}（岗位: {target_title}）")

        # 8. ★ 异常巡检告警：如果有降级事件，推送巡检自愈报告
        if HEALING_EVENTS:
            final_state = (
                "已基于现有实时情报生成折损版报告，并成功写入 Notion。"
                if notion_write_ok
                else "报告写入 Notion 失败，请检查日志。"
            )
            _send_healing_alert(chat_id, company, final_state)

    except Exception as e:
        logger.error(f"[Research] 深度背调流程异常: {e}", exc_info=True)
        _send_feishu_message(chat_id, f"❌ 深度背调执行失败: {e}")


def _chunk_text(text: str, chunk_size: int = 1500) -> list[str]:
    """
    文本切片器：将超长文本按 chunk_size 字符为单位切割。
    优先在换行符处切割，避免断词。
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # 尝试在最近的换行符处切割
        newline_pos = text.rfind("\n", start, end)
        if newline_pos > start:
            end = newline_pos
        chunks.append(text[start:end])
        start = end
    return chunks


def _markdown_to_notion_blocks(markdown_text: str) -> list[dict]:
    """
    将 Markdown 文本转换为 Notion block children API 可接受的 block 数组。
    支持：heading_2（##）、heading_3（###）、bulleted_list_item（-）、paragraph。

    自动切片：每段超过 1500 字符时，自动切割为多个 paragraph block，
    突破 Notion rich_text 2000 字符限制。
    """
    blocks = []
    for line in markdown_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": line[4:]}}]
                }
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                }
            })
        elif line.startswith("- "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                }
            })
        else:
            # 对超长段落进行自动切片，防止 Notion rich_text 2000 字符限制
            chunks = _chunk_text(line, chunk_size=1500)
            for chunk in chunks:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    }
                })
    return blocks


def _background_briefing(chat_id: str) -> None:
    """
    后台执行简报生成全流程（在独立线程中运行，不阻塞 WebSocket 主线程）：
      1. 查询 Notion 过去 24 小时新增岗位（仅按时间筛选，不限分数）
      2. 无新增时推送保活卡片
      3. 有则调用 AI 提炼核心匹配点，组装极客风格早报卡片
      4. 飞书推送格式化早报
    """
    try:
        jobs = query_notion_recent_24h()
        logger.info(f"[Briefing] 过去 24 小时查询到 {len(jobs)} 条岗位（仅时间筛选）")

        if not jobs:
            logger.info("[Briefing] 过去 24 小时无新入库岗位，推送无更新卡片")
            no_update_card = (
                "☀️ **InterviewOS | 今日简报 (无新增)**\n"
                "📅 统计周期：过去 24 小时（按 Notion 入库时间）\n"
                "📭 未找到 24h 内新入库的岗位。\n"
                "💡 可先发送「全面抓取」更新岗位库后再试。"
            )
            _send_feishu_message(chat_id, no_update_card)
            return

        # 3. AI 提炼核心匹配点 + 组装早报卡片
        report = generate_briefing_report(jobs)

        # 4. 飞书推送（如果简报太长，分段发送）
        max_len = 1500
        if len(report) <= max_len:
            _send_feishu_message(chat_id, report)
        else:
            for i in range(0, len(report), max_len):
                chunk = report[i:i + max_len]
                _send_feishu_message(chat_id, chunk)

        logger.info(f"[Briefing] 早报推送完成，共 {len(jobs)} 个岗位")

    except Exception as e:
        logger.error(f"[Briefing] 简报生成流程异常: {e}", exc_info=True)
        _send_feishu_message(chat_id, f"❌ 简报生成失败: {e}")


def trigger_daily_briefing(chat_id: str) -> None:
    """
    每日岗位早报定时任务入口。
    可在 cron / scheduler 中调用，非阻塞执行。

    参数:
        chat_id: 飞书会话 ID（早报推送目标）
    """
    logger.info(f"[DailyBriefing] 触发每日早报任务（目标会话: {chat_id}）")
    t = threading.Thread(target=_background_briefing, args=(chat_id,), daemon=True)
    t.start()
    logger.info("[DailyBriefing] 早报任务已在后台线程启动")


# ── ChatOps 按需爬虫（串行、非阻塞网关） ─────────────────


def parse_menu_guide_intent(text: str) -> Optional[str]:
    """解析飞书快捷菜单引导指令，命中则返回固定回复文案。"""
    normalized = text.strip()
    if not normalized:
        return None
    # 精确匹配（菜单按钮默认文案）
    if normalized in MENU_GUIDE_TRIGGERS:
        return MENU_GUIDE_TRIGGERS[normalized]
    # 容错：去掉 @机器人 前缀后再次匹配
    for key, reply in MENU_GUIDE_TRIGGERS.items():
        if key in normalized and len(normalized) <= len(key) + 4:
            return reply
    return None


def parse_spider_intent(text: str) -> Optional[dict]:
    """
    解析爬虫 ChatOps 意图（9 平台精确映射）。

    返回:
        {"mode": "full", "plan": [(script, label), ...]}
        {"mode": "single", "plan": [(script, label)], "label": "字节跳动"}
        None — 非爬虫指令
    """
    normalized = text.strip()
    if not normalized:
        return None

    if any(kw in normalized for kw in SPIDER_FULL_KEYWORDS):
        return {"mode": "full", "plan": list(SPIDER_FULL_PLAN), "label": "全平台（9平台）"}

    for trigger, script_name, platform_label in SPIDER_PLATFORM_RULES:
        if trigger in normalized:
            return {
                "mode": "single",
                "plan": [(script_name, platform_label)],
                "label": platform_label,
            }

    return None


def _run_spider_script(script_name: str, label: str) -> dict:
    """在后台线程中串行执行单个爬虫脚本（阻塞当前线程，不阻塞 WebSocket）。"""
    script_path = os.path.join(PROJECT_ROOT, script_name)
    started_at = datetime.now()
    t0 = time.time()

    if not os.path.isfile(script_path):
        return {
            "ok": False,
            "label": label,
            "script": script_name,
            "returncode": -1,
            "elapsed": 0,
            "error": f"脚本不存在: {script_path}",
        }

    logger.info(f"[Spider] 启动 {label} ({script_name})")
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=7200,
        )
        elapsed = time.time() - t0
        stderr_tail = (result.stderr or "")[-400:]
        if result.returncode == 0:
            logger.info(f"[Spider] ✅ {label} 完成 ({elapsed:.0f}s)")
            return {
                "ok": True,
                "label": label,
                "script": script_name,
                "returncode": result.returncode,
                "elapsed": elapsed,
                "started_at": started_at,
                "stderr_tail": stderr_tail,
            }
        logger.error(f"[Spider] ❌ {label} 退出码 {result.returncode}")
        return {
            "ok": False,
            "label": label,
            "script": script_name,
            "returncode": result.returncode,
            "elapsed": elapsed,
            "started_at": started_at,
            "error": f"退出码 {result.returncode}",
            "stderr_tail": stderr_tail,
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        return {
            "ok": False,
            "label": label,
            "script": script_name,
            "returncode": -1,
            "elapsed": elapsed,
            "started_at": started_at,
            "error": "执行超时（7200s）",
        }
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"[Spider] {label} 异常: {e}", exc_info=True)
        return {
            "ok": False,
            "label": label,
            "script": script_name,
            "returncode": -1,
            "elapsed": elapsed,
            "started_at": started_at,
            "error": str(e),
        }


def _run_notion_bridge() -> dict:
    """全量抓取结束后触发 Notion 桥接。"""
    t0 = time.time()
    output_file = os.path.join(PROJECT_ROOT, "data", "openclaw_jobs.json")
    if not os.path.isfile(output_file):
        return {"ok": False, "label": "Notion同步", "error": "data/openclaw_jobs.json 不存在", "elapsed": 0}
    if os.path.getsize(output_file) < 1024:
        return {"ok": False, "label": "Notion同步", "error": "openclaw_jobs.json 过小，跳过同步", "elapsed": 0}
    if not os.path.isfile(BRIDGE_SCRIPT):
        return {"ok": False, "label": "Notion同步", "error": "openclaw_bridge.py 不存在", "elapsed": 0}

    env = dict(os.environ)
    env["FORCE_UPDATE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, BRIDGE_SCRIPT],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=1800,
        )
        elapsed = time.time() - t0
        if result.returncode == 0:
            return {"ok": True, "label": "Notion同步", "elapsed": elapsed, "returncode": 0}
        return {
            "ok": False,
            "label": "Notion同步",
            "elapsed": elapsed,
            "returncode": result.returncode,
            "error": f"桥接脚本退出码 {result.returncode}",
            "stderr_tail": (result.stderr or "")[-400:],
        }
    except Exception as e:
        return {"ok": False, "label": "Notion同步", "elapsed": time.time() - t0, "error": str(e)}


def _format_spider_result_card(result: dict) -> str:
    """组装单平台抓取结果卡片（文本 Markdown）。"""
    label = result.get("label", "未知平台")
    elapsed = int(result.get("elapsed", 0))
    script = result.get("script", "")
    started_at = result.get("started_at")
    time_str = started_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(started_at, datetime) else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if result.get("ok"):
        lines = [
            f"✅ **{label} 岗位抓取完成**",
            f"⏱️ 开始时间：{time_str}",
            f"⌛ 耗时：{elapsed} 秒",
            f"📜 脚本：`{script}`" if script else "",
            f"📊 退出码：{result.get('returncode', 0)}",
        ]
    else:
        lines = [
            f"❌ **{label} 岗位抓取失败**",
            f"⏱️ 开始时间：{time_str}",
            f"⌛ 耗时：{elapsed} 秒",
            f"📜 脚本：`{script}`" if script else "",
            f"📊 退出码：{result.get('returncode', 'N/A')}",
            f"❗ 原因：{result.get('error', '未知错误')}",
        ]
        stderr_tail = result.get("stderr_tail", "").strip()
        if stderr_tail:
            lines.append(f"📝 日志尾部：\n```\n{stderr_tail}\n```")

    return "\n".join(line for line in lines if line)


def _background_crawl_serial(plan: list[tuple[str, str]], chat_id: str, *, run_bridge: bool) -> None:
    """
    后台串行执行爬虫计划；每个脚本结束后推送结果卡片。
    全量模式：严格串行，单平台失败仅记录并 continue，不阻断后续脚本（防 OOM + 容灾）。
    全量结束后可选触发 Notion 桥接。
    """
    success_count = 0
    fail_count = 0
    try:
        for script_name, label in plan:
            try:
                result = _run_spider_script(script_name, label)
            except Exception as e:
                logger.error(f"[Spider] {label} 未预期异常，跳过并继续: {e}", exc_info=True)
                result = {
                    "ok": False,
                    "label": label,
                    "script": script_name,
                    "returncode": -1,
                    "elapsed": 0,
                    "started_at": datetime.now(),
                    "error": f"调度层异常（已跳过）: {e}",
                }
            if result.get("ok"):
                success_count += 1
            else:
                fail_count += 1
                logger.warning(f"[Spider] {label} 失败，继续下一个平台（{fail_count}/{len(plan)}）")
            _send_feishu_message(chat_id, _format_spider_result_card(result))

        if run_bridge:
            bridge_result = _run_notion_bridge()
            _send_feishu_message(chat_id, _format_spider_result_card({
                **bridge_result,
                "script": "openclaw_bridge.py",
                "started_at": datetime.now(),
            }))

        if len(plan) > 1:
            summary = (
                f"📦 **全平台抓取批次结束**\n"
                f"✅ 成功：{success_count} | ❌ 失败：{fail_count} | 共 {len(plan)} 个平台"
            )
            if run_bridge:
                summary += "\n🔄 已尝试执行 Notion 同步桥接"
            _send_feishu_message(chat_id, summary)

    except Exception as e:
        logger.error(f"[Spider] 串行抓取流程异常: {e}", exc_info=True)
        _send_feishu_message(chat_id, f"❌ 爬虫调度异常: {e}\n{traceback.format_exc()[-500:]}")
    finally:
        _crawl_lock.release()
        logger.info("[Spider] 抓取任务结束，已释放互斥锁")


def _start_spider_crawl(intent: dict, chat_id: str) -> Optional[str]:
    """
    尝试启动爬虫后台任务。成功返回即时确认文案；若已有任务在跑则返回占用提示。
    """
    if not _crawl_lock.acquire(blocking=False):
        return "⚠️ 已有抓取任务正在串行执行中，请等待当前批次完成后再下发新指令。"

    mode = intent["mode"]
    label = intent["label"]
    plan = intent["plan"]
    run_bridge = mode == "full"

    if mode == "full":
        ack = (
            f"⏳ 收到指令，已启动**全平台**岗位抓取（共 {len(plan)} 个平台，严格串行）。"
            f"为保护本地内存，采用串行安全模式运行；失败平台将自动跳过并继续，"
            f"每完成一个平台将推送结果卡片…"
        )
    else:
        ack = (
            f"⏳ 收到指令，已启动**{label}**岗位抓取。"
            f"任务在后台 subprocess 执行，不阻塞网关；完成后将推送结果卡片…"
        )

    t = threading.Thread(
        target=_background_crawl_serial,
        args=(plan, chat_id),
        kwargs={"run_bridge": run_bridge},
        daemon=True,
    )
    t.start()
    logger.info(f"[Spider] 后台串行任务已启动 mode={mode} platforms={len(plan)}")
    return ack


# ── 意图识别与路由 ────────────────────────────────────────


def route_intent(text: str, chat_id: str) -> Optional[str]:
    """
    根据用户输入的文本进行意图识别，返回回复内容。
    场景 A：菜单引导（背调指南 / 抓取官网指南）→ 固定文案
    场景 B：按需爬虫（9 平台 / 全面抓取）→ 后台串行 subprocess
    场景 C：包含"分析"/"背调"/"研究"/"看看" → 深度分析（后台异步执行）
    场景 D：包含"早报"/"简报"/"新岗位"/"汇总" → 简报生成（后台异步执行）
    场景 E：兜底回复

    注意：此函数仅返回回复文本，不发送飞书消息。
    飞书消息的发送统一由 do_p2_im_message_receive_v1 处理，避免重复发送。

    AI 资讯菜单已在 do_p2_im_message_receive_v1 最前转发 Node，不会进入本函数。
    """
    # ════════════════════════════════════════════════════════════
    # ⚡ 场景 0：系统拦截层（本函数内最高优先级）
    # 用户说"停止分析"、"取消"、"别弄了"等，必须立即拦截，
    # 绝对不能因为包含"分析"二字而误触发深度分析路由。
    #
    # 规则：
    #   1. 命中关键词后立即打印日志：[Router] 识别到紧急中止指令
    #   2. 回复飞书用户："🛑 已收到中止指令，网关已停止下发新任务。"
    #   3. 立刻 return，绝对不允许代码继续往下匹配其他场景！
    # ════════════════════════════════════════════════════════════
    normalized_route = text.strip()
    if normalized_route in AI_HOT_MENU_COMMANDS:
        logger.warning(
            "[Router] AI 资讯指令误入 route_intent，应已在消息入口拦截: %s",
            normalized_route,
        )
        return None

    stop_keywords = ["停止", "取消", "终止", "别弄了", "stop"]
    if any(kw in text for kw in stop_keywords):
        logger.info(f"[Router] 识别到紧急中止指令: {text}")
        return (
            "🛑 已收到中止指令，网关已停止下发新任务。"
        )

    # ── 场景 A：飞书 ChatOps 快捷菜单引导（须在「背调」关键词之前） ──
    menu_reply = parse_menu_guide_intent(text)
    if menu_reply:
        logger.info(f"[Router] 识别到菜单引导指令: {text.strip()}")
        return menu_reply

    # ── 场景 B：按需爬虫 ChatOps（9 平台精确映射，优先于背调） ──
    spider_intent = parse_spider_intent(text)
    if spider_intent:
        logger.info(f"[Router] 识别到爬虫指令 mode={spider_intent['mode']}: {text}")
        return _start_spider_crawl(spider_intent, chat_id)

    # ── 场景 C：深度分析（须含公司名或明确句式；菜单「深度背调」已在场景 A 拦截） ──
    if is_research_command(text):
        logger.info(f"[Router] 识别到深度分析指令: {text}")
        # 在后台线程中执行耗时操作，不阻塞主线程
        t = threading.Thread(target=_background_research, args=(text, chat_id), daemon=True)
        t.start()
        return (
            "🔍 收到指令。正在唤醒 OpenClaw 去 Notion 检索该公司的最新岗位，"
            "并执行深度背调，请稍候..."
        )

    # ── 场景 D：简报生成（含菜单「今日简报」） ──
    normalized = text.strip()
    briefing_triggers = ("今日简报", "早报", "简报", "新岗位", "汇总")
    if normalized == "今日简报" or (
        any(kw in text for kw in briefing_triggers)
        and normalized not in MENU_GUIDE_TRIGGERS
    ):
        logger.info("[Router] 识别到简报生成指令")
        t = threading.Thread(target=_background_briefing, args=(chat_id,), daemon=True)
        t.start()
        return (
            "📊 正在读取 Notion 数据库过去 24 小时的数据，"
            "为您生成今日岗位简报..."
        )

    # ── 场景 E：兜底回复 ──
    logger.info("[Router] 未识别到特定指令，使用兜底回复")
    return (
        "🤖 我是 InterviewOS 中枢。你可以对我说：\n"
        "• 点底部菜单 **背调指南** / **抓取官网指南** 查看用法\n"
        "• **抓取BOSS直聘** / **抓取字节跳动** 等 — 单平台抓取（9 平台）\n"
        "• **全面抓取** — 9 平台严格串行全量抓取\n"
        "• **帮我背调一下字节跳动** — OpenClaw 深度背调\n"
        "• **今日简报** — 24h 内新入库岗位（不限分数）"
    )


# ── 事件处理函数 ──────────────────────────────────────────


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    处理 im.message.receive_v1 事件：
      1. 提取用户发送的纯文本
      2. 意图识别路由
      3. 终端回显 + 飞书自动回复（仅发送一次，避免重复）

    修复重复发送 Bug（核心策略）：
      - 路由判断后立即 return 释放飞书 Webhook，防止飞书因超时而重发
      - 耗时的 OpenClaw 调用已在 route_intent 中通过 threading.Thread 放入后台
      - route_intent 不再发送飞书消息，只返回文本
      - 所有飞书消息发送统一在此函数中执行
      - 后台线程中的 _send_feishu_message 是异步进度通知，与主流程不冲突
    """
    try:
        event = data.event
        if event is None:
            return

        message = event.message
        sender = event.sender

        if message is None:
            return

        # ── 基于 message_id 的去重（最高优先级） ──
        # 飞书存在"至少投递一次"的重传机制，相同 message_id 可能被多次推送
        msg_id = getattr(message, "message_id", None) or ""
        if msg_id:
            if msg_id in processed_message_ids:
                logger.info(f"[Deduplication] 拦截到飞书重传的重复消息，直接忽略: {msg_id}")
                return
            processed_message_ids.append(msg_id)

        chat_id = message.chat_id or ""
        msg_type = message.message_type or ""
        content_raw = message.content or "{}"
        # 从 content_raw JSON 中提取纯文本
        try:
            content_data = json.loads(content_raw)
            text = content_data.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = content_raw
        # 去除 @bot 前缀（飞书群聊中 @机器人 会带上）
        text = re.sub(r"@_user_\d+\s*", "", text).strip()

        if not text:
            logger.info("⏭️ 跳过空消息")
            return

        # ════════════════════════════════════════════════════════════
        # 🚨 最高优先级：AI 资讯雷达菜单（必须在背调/爬虫/停止指令等之前）
        # 命中后转发 Node.js :3001，return 阻断，绝不进入 route_intent
        # ════════════════════════════════════════════════════════════
        if try_forward_ai_hot_news_to_node(text, chat_id):
            return

        # ── 终端回显 ──
        sender_id = sender.sender_id.open_id if sender and sender.sender_id else "unknown"
        logger.info(f"📩 收到飞书指令: {text}")
        logger.info(f"   来自: {sender_id}")
        logger.info(f"   群聊/私聊: {chat_id}")

        # ── 意图识别路由（route_intent 不再发送消息，只返回文本） ──
        reply_text = route_intent(text, chat_id)
        if reply_text:
            _send_feishu_message(chat_id, reply_text)
            logger.info(f"✅ 已回复确认消息（仅发送一次）")
        else:
            logger.info(f"⏭️ route_intent 返回空，跳过回复")

        # ⚡ 立即 return 释放飞书 Webhook，防止飞书因超时而重发消息
        # 耗时的 OpenClaw 调用已在 route_intent 中通过 threading.Thread 放入后台
        return

    except Exception as e:
        logger.error(f"❌ 处理消息事件异常: {e}", exc_info=True)


# ── 主入口 ────────────────────────────────────────────────


def main():
    """启动飞书 WebSocket 长连接网关"""

    logger.info("=" * 50)
    logger.info("🚀 InterviewOS 飞书 WebSocket 网关启动中...")
    logger.info(f"   APP_ID: {str(APP_ID)[:8]}...{str(APP_ID)[-4:]}")
    logger.info("   监听事件: im.message.receive_v1")
    logger.info("   路由场景: AI资讯→Node / 菜单引导 / 9平台爬虫 / 深度分析 / 简报 / 兜底")
    logger.info(f"   Node 卡片 API: {NODE_NEWS_CARD_API}")
    logger.info("=" * 50)

    # 检查密钥是否已配置
    if APP_ID == "YOUR_APP_ID" or APP_SECRET == "YOUR_APP_SECRET":
        logger.warning("⚠️  飞书密钥未配置！请在 .env 文件中设置：")
        logger.warning("   FEISHU_APP_ID=你的应用ID")
        logger.warning("   FEISHU_APP_SECRET=你的应用密钥")
        logger.warning("请手动填入密钥后重新运行。")
        return

    try:
        # 注册事件处理器
        logger.info("🔄 正在注册事件处理器...")
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
            .build()
        logger.info("✅ 事件处理器注册完成")

        # 启动 WebSocket 客户端
        logger.info("🔄 准备调起飞书 WebSocket 客户端...")
        cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=event_handler)
        logger.info("✅ WebSocket 客户端已构建，正在连接飞书长连接网关...")
        cli.start()

    except KeyboardInterrupt:
        logger.info("👋 收到中断信号，网关关闭中...")
    except Exception as e:
        logger.error(f"💥 网关启动惨遭失败，真实报错原因是: {e}", exc_info=True)
    finally:
        logger.info("🏁 飞书网关已关闭")


if __name__ == "__main__":
    logger.info("🔥 feishu_gateway.py 脚本开始执行")
    main()
