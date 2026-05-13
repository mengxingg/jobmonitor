"""
Notion 同步模块

将抓取并评估后的岗位数据写入 Notion JobMonitor 数据库。
包含去重逻辑：按 URL 查询，已存在则更新，不存在则新建。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from config import NOTION_API_KEY, NOTION_JOBS_DB

logger = logging.getLogger(__name__)

# ── Notion API 常量 ──

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

# ── 辅助函数 ──


def _list_to_bullets(items: list[str]) -> str:
    """将字符串列表转换为带 bullet point 的文本"""
    if not items:
        return ""
    return "\n".join(f"• {item}" for item in items)


# ── 字段映射 ──
#
# Notion 数据库字段名（中文） -> 写入值
# 请确保你的 Notion JobMonitor 数据库包含以下字段：
#
#   Title            (title)       - 岗位名称
#   Company          (rich_text)   - 公司名称
#   Platform         (rich_text)   - 来源平台（如 "BOSS直聘"）
#   URL              (url)         - 岗位链接（去重主键）
#   Location         (rich_text)   - 工作地点
#   Remote           (rich_text)   - 远程/混合/现场
#   Salary Range     (rich_text)   - 薪资范围（注意中间有空格）
#   JD Summary       (rich_text)   - 职位描述摘要
#   Match Score      (number)      - AI 匹配评分 (0-100)
#   Match Reasons    (rich_text)   - AI 匹配优势（bullet point 列表）
#   Mismatch Reasons (rich_text)   - AI 匹配不足（bullet point 列表）
#   Status           (select)      - 状态：新发现/已查看/已解码/已投递/已放弃
#   Priority         (select)      - 优先级：高/中/低
#   Discovered Date  (date)        - 发现日期
#   Notes            (rich_text)   - AI 总体评价（summary）
#


def find_existing_by_url(url: str) -> Optional[str]:
    """
    按 URL 查询是否已存在该岗位（使用 requests 直接调用 Notion API）。

    向 https://api.notion.com/v1/databases/{NOTION_JOBS_DB}/query 发送 POST 请求，
    匹配 URL 字段（同时尝试 rich_text 和 url 两种类型）等于当前 URL 的记录。

    返回:
        已存在时返回 page_id，否则返回 None
    """
    if not url:
        return None

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    # 同时尝试 rich_text 和 url 两种字段类型，兼容不同数据库配置
    filter_body = {
        "filter": {
            "or": [
                {
                    "property": "URL",
                    "rich_text": {"equals": url},
                },
                {
                    "property": "URL",
                    "url": {"equals": url},
                },
            ]
        },
        "page_size": 1,
    }

    try:
        resp = requests.post(
            f"{NOTION_BASE_URL}/databases/{NOTION_JOBS_DB}/query",
            headers=headers,
            json=filter_body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0]["id"]
        return None
    except requests.RequestException as e:
        logger.error("查询 Notion 失败 (URL=%s): %s", url, e)
        return None


def _build_properties(
    title: str,
    company: str,
    platform: str,
    url: str,
    location: str,
    remote: str = "",
    salary_range: str = "",
    jd_summary: str = "",
    match_score: int = 0,
    match_reasons: Optional[list[str]] = None,
    mismatch_reasons: Optional[list[str]] = None,
    status: str = "",
    priority: str = "",
    discovered_date: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """构建 Notion properties 字典（create / update 共用）"""
    properties: dict[str, Any] = {
        "Title": {"title": [{"text": {"content": title[:200]}}]},
        "Company": {"rich_text": [{"text": {"content": company[:200]}}]},
        "Platform": {"rich_text": [{"text": {"content": platform[:50]}}]},
        "URL": {"url": url if url else None},
        "Location": {"rich_text": [{"text": {"content": location[:200]}}]},
        "Salary Range": {"rich_text": [{"text": {"content": salary_range[:200]}}]},
        "Match Score": {"number": match_score},
    }

    # 可选字段：仅在有值时写入
    if status:
        properties["Status"] = {"select": {"name": status[:20]}}
    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes[:2000]}}]}
    if remote:
        properties["Remote"] = {"rich_text": [{"text": {"content": remote[:20]}}]}
    if jd_summary:
        properties["JD Summary"] = {"rich_text": [{"text": {"content": jd_summary[:2000]}}]}
    if priority:
        properties["Priority"] = {"select": {"name": priority[:10]}}
    if discovered_date:
        properties["Discovered Date"] = {"date": {"start": discovered_date[:20]}}

    # Match Reasons / Mismatch Reasons：列表转 bullet point 字符串
    if match_reasons:
        properties["Match Reasons"] = {
            "rich_text": [{"text": {"content": _list_to_bullets(match_reasons)[:2000]}}]
        }
    if mismatch_reasons:
        properties["Mismatch Reasons"] = {
            "rich_text": [{"text": {"content": _list_to_bullets(mismatch_reasons)[:2000]}}]
        }

    return properties


def _notion_headers() -> dict[str, str]:
    """构建 Notion API 通用请求头"""
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def create_job_page(
    title: str,
    company: str,
    platform: str,
    url: str,
    location: str,
    remote: str = "",
    salary_range: str = "",
    jd_summary: str = "",
    match_score: int = 0,
    match_reasons: Optional[list[str]] = None,
    mismatch_reasons: Optional[list[str]] = None,
    status: str = "新发现",
    priority: str = "",
    discovered_date: str = "",
    notes: str = "",
) -> Optional[str]:
    """
    在 Notion JobMonitor 数据库中创建新页面（使用 requests）。

    返回:
        成功时返回 page_id，失败返回 None
    """
    try:
        properties = _build_properties(
            title=title,
            company=company,
            platform=platform,
            url=url,
            location=location,
            remote=remote,
            salary_range=salary_range,
            jd_summary=jd_summary,
            match_score=match_score,
            match_reasons=match_reasons,
            mismatch_reasons=mismatch_reasons,
            status=status,
            priority=priority,
            discovered_date=discovered_date,
            notes=notes,
        )

        body = {
            "parent": {"database_id": NOTION_JOBS_DB},
            "properties": properties,
        }

        resp = requests.post(
            f"{NOTION_BASE_URL}/pages",
            headers=_notion_headers(),
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        page_id = data["id"]
        logger.info("Notion 新建成功: %s (%s) - page_id=%s", title, company, page_id)
        return page_id

    except Exception as e:
        logger.error("Notion 新建失败 [%s - %s]: %s", company, title, e)
        return None


def update_job_page(
    page_id: str,
    title: str,
    company: str,
    platform: str,
    url: str,
    location: str,
    remote: str = "",
    salary_range: str = "",
    jd_summary: str = "",
    match_score: int = 0,
    match_reasons: Optional[list[str]] = None,
    mismatch_reasons: Optional[list[str]] = None,
    status: str = "",
    priority: str = "",
    discovered_date: str = "",
    notes: str = "",
) -> bool:
    """
    更新 Notion 中已存在的岗位页面（使用 requests）。

    返回:
        成功 True，失败 False
    """
    try:
        properties = _build_properties(
            title=title,
            company=company,
            platform=platform,
            url=url,
            location=location,
            remote=remote,
            salary_range=salary_range,
            jd_summary=jd_summary,
            match_score=match_score,
            match_reasons=match_reasons,
            mismatch_reasons=mismatch_reasons,
            status=status,
            priority=priority,
            discovered_date=discovered_date,
            notes=notes,
        )

        resp = requests.patch(
            f"{NOTION_BASE_URL}/pages/{page_id}",
            headers=_notion_headers(),
            json={"properties": properties},
            timeout=15,
        )
        resp.raise_for_status()

        logger.info("Notion 更新成功: %s (%s) - page_id=%s", title, company, page_id)
        return True

    except Exception as e:
        logger.error("Notion 更新失败 [%s - %s]: %s", company, title, e)
        return False


def sync_job(
    title: str,
    company: str,
    platform: str,
    url: str,
    location: str,
    remote: str = "",
    salary_range: str = "",
    jd_summary: str = "",
    match_score: int = 0,
    match_reasons: Optional[list[str]] = None,
    mismatch_reasons: Optional[list[str]] = None,
    status: str = "新发现",
    priority: str = "",
    discovered_date: str = "",
    notes: str = "",
) -> bool:
    """
    同步单条岗位到 Notion（含去重逻辑）。

    流程:
        1. 按 URL 查询是否已存在
        2. 已存在 → 更新
        3. 不存在 → 新建

    返回:
        成功 True，失败 False
    """
    if not title and not company:
        logger.warning("跳过空数据: title=%s, company=%s", title, company)
        return False

    existing_id = find_existing_by_url(url) if url else None

    if existing_id:
        logger.info("URL 已存在，执行更新: %s", url)
        return update_job_page(
            page_id=existing_id,
            title=title,
            company=company,
            platform=platform,
            url=url,
            location=location,
            remote=remote,
            salary_range=salary_range,
            jd_summary=jd_summary,
            match_score=match_score,
            match_reasons=match_reasons,
            mismatch_reasons=mismatch_reasons,
            status=status,
            priority=priority,
            discovered_date=discovered_date,
            notes=notes,
        )
    else:
        logger.info("新建岗位: %s (%s)", title, company)
        return create_job_page(
            title=title,
            company=company,
            platform=platform,
            url=url,
            location=location,
            remote=remote,
            salary_range=salary_range,
            jd_summary=jd_summary,
            match_score=match_score,
            match_reasons=match_reasons,
            mismatch_reasons=mismatch_reasons,
            status=status,
            priority=priority,
            discovered_date=discovered_date,
            notes=notes,
        ) is not None


def sync_jobs(jobs: list[dict]) -> dict:
    """
    批量同步岗位到 Notion。

    参数:
        jobs: 字典列表，每项包含 title, company, platform, url,
              location, remote, salary_range, jd_summary,
              match_score, match_reasons, mismatch_reasons,
              status, priority, discovered_date, notes

    返回:
        {"success": int, "failed": int, "total": int}
    """
    success = 0
    failed = 0

    for job in jobs:
        ok = sync_job(
            title=job.get("title", ""),
            company=job.get("company", ""),
            platform=job.get("platform", ""),
            url=job.get("url", ""),
            location=job.get("location", ""),
            remote=job.get("remote", ""),
            salary_range=job.get("salary_range", ""),
            jd_summary=job.get("jd_summary", ""),
            match_score=job.get("match_score", 0),
            match_reasons=job.get("match_reasons"),
            mismatch_reasons=job.get("mismatch_reasons"),
            status=job.get("status", "新发现"),
            priority=job.get("priority", ""),
            discovered_date=job.get("discovered_date", ""),
            notes=job.get("notes", ""),
        )
        if ok:
            success += 1
        else:
            failed += 1

    total = len(jobs)
    logger.info("批量同步完成: 成功 %s / 失败 %s / 总计 %s", success, failed, total)
    return {"success": success, "failed": failed, "total": total}
