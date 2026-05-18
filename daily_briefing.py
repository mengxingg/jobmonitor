"""
daily_briefing.py — 每日岗位早报定时任务入口

通过 cron 或 scheduler 调用，非阻塞执行：
  1. 查询 Notion 过去 24 小时新增高分岗位（Match Score >= 80）
  2. 如果无新增高分岗位，静默退出，不打扰用户
  3. 有则调用 AI 提炼核心匹配点，组装极客风格早报卡片
  4. 通过飞书 API 推送格式化早报

用法:
  conda run -n job_env python daily_briefing.py                          # 推送到默认会话
  conda run -n job_env python daily_briefing.py --chat_id=oc_xxxxx       # 推送到指定会话

crontab 配置（每天早上 9:00 推送）:
  0 9 * * * cd /Users/gmx/interview/job_engine && conda run -n job_env python daily_briefing.py >> logs/daily_briefing.log 2>&1
"""

import sys
import io
import json
import logging
import os
import traceback
from datetime import datetime, timedelta
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
logger = logging.getLogger("daily_briefing")

PROJECT_DIR = Path(__file__).parent

# ── 加载 .env ──
from dotenv import load_dotenv
_env_path = PROJECT_DIR / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
# 早报推送目标会话 ID（可在 .env 中配置 BRIEFING_CHAT_ID，或通过 --chat_id 参数传入）
BRIEFING_CHAT_ID = os.getenv("BRIEFING_CHAT_ID", "")


# ── 飞书消息发送 ──


def _get_feishu_tenant_token() -> Optional[str]:
    """获取飞书 tenant_access_token"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.error("[Feishu] 飞书凭证未配置")
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
        logger.error(f"[Feishu] 获取 token 失败: {resp.status_code}")
        return None
    except Exception as e:
        logger.error(f"[Feishu] 获取 token 异常: {e}")
        return None


def _send_feishu_message(chat_id: str, text: str) -> bool:
    """向飞书发送文本消息"""
    token = _get_feishu_tenant_token()
    if not token:
        return False
    try:
        import requests
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=10,
        )
        if resp.ok:
            logger.info(f"[Feishu] 消息已发送至 {chat_id}")
            return True
        logger.error(f"[Feishu] 发送失败: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        logger.error(f"[Feishu] 发送异常: {e}")
        return False


# ── Notion 查询 ──


def query_high_score_jobs_24h(min_score: int = 80) -> list[dict]:
    """
    查询 Notion 数据库中过去 24 小时内新增的高分岗位。
    筛选条件：Discovered Date 属于过去 24 小时，且 Match Score >= min_score。
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.error("[Notion] API 密钥或数据库 ID 未配置")
        return []

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    yesterday = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    filter_body = {
        "filter": {
            "and": [
                {"property": "Discovered Date", "date": {"on_or_after": yesterday}},
                {"property": "Match Score", "number": {"greater_than_or_equal_to": min_score}},
            ],
        },
        "sorts": [{"property": "Match Score", "direction": "descending"}],
        "page_size": 20,
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
        logger.info(f"[Notion] 过去 24 小时查询返回 {len(results)} 条高分岗位")

        jobs = []
        for page in results:
            props = page.get("properties", {})
            title_field = props.get("Title", {}).get("title", [])
            title = title_field[0].get("text", {}).get("content", "") if title_field else ""
            company_field = props.get("Company", {}).get("rich_text", [])
            company = company_field[0].get("text", {}).get("content", "") if company_field else ""
            score = props.get("Match Score", {}).get("number", 0) or 0
            location_field = props.get("Location", {}).get("rich_text", [])
            location = location_field[0].get("text", {}).get("content", "") if location_field else ""
            salary_field = props.get("Salary Range", {}).get("rich_text", [])
            salary = salary_field[0].get("text", {}).get("content", "") if salary_field else ""

            jobs.append({
                "page_id": page["id"],
                "title": title,
                "company": company,
                "score": score,
                "location": location,
                "salary": salary,
            })

        return jobs

    except Exception as e:
        logger.error(f"[Notion] 查询失败: {e}", exc_info=True)
        return []


# ── AI 提炼核心匹配点 ──


def _generate_match_points(jobs: list[dict]) -> list[str]:
    """调用 AI 为每个岗位生成核心匹配点（15 字以内）"""
    if not jobs:
        return []

    jobs_text = ""
    for i, job in enumerate(jobs, 1):
        jobs_text += (
            f"{i}. {job.get('title', '未知')} @ {job.get('company', '未知')}\n"
            f"   薪资: {job.get('salary', '面议')} | 地点: {job.get('location', '未知')}\n"
        )

    prompt = f"""以下是过去 24 小时内新增的高分 AI 产品经理岗位列表（匹配度 >= 80）。

## 岗位列表
{jobs_text}

## 任务
请为每个岗位生成一条"核心匹配点"（一句话，15 字以内），说明为什么这个岗位适合候选人。

## 输出格式要求
请严格按以下格式输出，每行一个岗位，不要有多余内容：

公司A | 岗位A | 匹配点一句话
公司B | 岗位B | 匹配点一句话
..."""

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
        points = []
        for line in output.strip().split("\n"):
            parts = [p.strip() for p in line.split("|")]
            points.append(parts[2] if len(parts) >= 3 else "AI 产品经理方向")
        logger.info(f"[AI] 匹配点提炼完成，共 {len(points)} 条")
        return points
    except Exception as e:
        logger.warning(f"[AI] 匹配点提炼失败: {e}")
        return ["AI 产品经理方向"] * len(jobs)


# ── 组装早报卡片 ──


def _build_briefing_card(jobs: list[dict], match_points: list[str]) -> str:
    """组装极客风格早报卡片"""
    lines = [
        "☀️ **InterviewOS | 今日高分岗位早报**",
        "📅 统计周期：过去 24 小时",
        f"📈 今日新收录高分岗位：{len(jobs)} 个",
        "",
        "━━━━━━━━━━━━━━━━━━",
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
        lines.append(f"🎯 核心匹配点：{match_pt}")
        if notion_link:
            lines.append(f"🔗 [查看详情]({notion_link})")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("💡 *提示：你可以直接在飞书对我说\"帮我背调一下[公司名]\"，唤醒 OpenClaw 执行深度情报深挖。*")

    return "\n".join(lines)


# ── 主流程 ──


def run_daily_briefing(chat_id: str) -> bool:
    """
    执行每日早报全流程。

    参数:
        chat_id: 飞书会话 ID

    返回:
        True 表示成功推送，False 表示失败或无数据
    """
    logger.info(f"[Briefing] 开始执行每日早报（目标会话: {chat_id}）")

    # 1. 查询 Notion
    jobs = query_high_score_jobs_24h(min_score=80)
    if not jobs:
        logger.info("[Briefing] 过去 24 小时无新增高分岗位，静默退出")
        return False

    # 2. AI 提炼匹配点
    match_points = _generate_match_points(jobs)
    while len(match_points) < len(jobs):
        match_points.append("AI 产品经理方向")

    # 3. 组装早报卡片
    card = _build_briefing_card(jobs, match_points)

    # 4. 飞书推送（分段发送）
    max_len = 1500
    if len(card) <= max_len:
        ok = _send_feishu_message(chat_id, card)
    else:
        ok = True
        for i in range(0, len(card), max_len):
            chunk = card[i:i + max_len]
            if not _send_feishu_message(chat_id, chunk):
                ok = False

    if ok:
        logger.info(f"[Briefing] ✅ 早报推送完成，共 {len(jobs)} 个岗位")
    else:
        logger.error("[Briefing] ❌ 早报推送失败")
    return ok


# ── 入口 ──


def main():
    """命令行入口"""
    # 解析 --chat_id 参数
    chat_id = BRIEFING_CHAT_ID
    for arg in sys.argv[1:]:
        if arg.startswith("--chat_id="):
            chat_id = arg.split("=", 1)[1]

    if not chat_id:
        logger.error("❌ 未指定早报推送目标会话 ID。请通过以下方式之一配置：")
        logger.error("   1. 在 .env 中设置 BRIEFING_CHAT_ID=oc_xxxxx")
        logger.error("   2. 命令行参数: --chat_id=oc_xxxxx")
        sys.exit(1)

    logger.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 启动每日早报任务")
    success = run_daily_briefing(chat_id)
    if success:
        logger.info("✅ 每日早报任务完成")
    else:
        logger.info("⏭️ 每日早报任务完成（无数据或推送失败）")


if __name__ == "__main__":
    main()
