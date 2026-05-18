"""
Notion 同步模块

将抓取并评估后的岗位数据写入 Notion JobMonitor 数据库。
包含去重逻辑：按 URL 查询，已存在则更新，不存在则新建。
"""

from __future__ import annotations

import logging
import time
import random
from typing import Any, Optional

import requests

from config import NOTION_API_KEY, NOTION_JOBS_DB

logger = logging.getLogger(__name__)

# ── 重试配置 ──
NOTION_MAX_RETRIES = 3
NOTION_RETRY_BASE_DELAY = 2.0
NOTION_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

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


def _notion_request_with_retry(
    method: str,
    url: str,
    **kwargs,
) -> requests.Response:
    """
    带指数退避重试的 Notion API 请求。

    对可重试的状态码（429, 500, 502, 503, 504）和网络异常自动重试。
    """
    last_exc = None
    for attempt in range(1, NOTION_MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in NOTION_RETRYABLE_STATUSES and attempt < NOTION_MAX_RETRIES:
                delay = NOTION_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "[RETRY] Notion API %s %s 返回 %d (第%d次, %.1f秒后重试)",
                    method, url, resp.status_code, attempt, delay,
                )
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < NOTION_MAX_RETRIES:
                delay = NOTION_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "[RETRY] Notion API 网络异常 (第%d次, %.1f秒后重试): %s",
                    attempt, delay, e,
                )
                time.sleep(delay)
                continue
        except requests.RequestException as e:
            last_exc = e
            if attempt < NOTION_MAX_RETRIES:
                delay = NOTION_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "[RETRY] Notion API 请求异常 (第%d次, %.1f秒后重试): %s",
                    attempt, delay, e,
                )
                time.sleep(delay)
                continue
            raise

    raise last_exc or RuntimeError(f"Notion API 请求失败 (已重试{NOTION_MAX_RETRIES}次)")


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
        resp = _notion_request_with_retry(
            "POST",
            f"{NOTION_BASE_URL}/databases/{NOTION_JOBS_DB}/query",
            headers=headers,
            json=filter_body,
            timeout=15,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0]["id"]
        return None
    except Exception as e:
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
        # Notion rich_text 字段有长度限制，截断到 1500 字符防止 API 报错
        properties["JD Summary"] = {"rich_text": [{"text": {"content": jd_summary[:1500]}}]}
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


def _jd_body_to_blocks(full_jd_body: str) -> list[dict]:
    """
    将完整 JD 文本转换为 Notion Block 列表。
    识别『职位描述』『职位要求』等标题，自动转为 heading_2 样式。
    """
    import re

    if not full_jd_body:
        return []

    # 识别标题的正则模式
    heading_patterns = [
        r"^【职位描述】",
        r"^【任职要求】",
        r"^职位描述[：:]",
        r"^岗位职责[：:]",
        r"^职位要求[：:]",
        r"^任职要求[：:]",
        r"^岗位要求[：:]",
        r"^任职资格[：:]",
        r"^Job Description[：:]",
        r"^Requirements[：:]",
        r"^Qualifications[：:]",
    ]
    heading_re = re.compile("|".join(heading_patterns), re.IGNORECASE)

    blocks = []
    max_block_len = 2000
    lines = full_jd_body.split("\n")
    current_paragraph = ""

    def flush_paragraph():
        nonlocal current_paragraph
        if not current_paragraph.strip():
            current_paragraph = ""
            return
        text = current_paragraph.strip()
        current_paragraph = ""
        if not text:
            return
        if len(text) <= max_block_len:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                },
            })
        else:
            for i in range(0, len(text), max_block_len):
                chunk = text[i:i + max_block_len]
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    },
                })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if heading_re.match(stripped):
            flush_paragraph()
            heading_text = heading_re.sub("", stripped).strip()
            if not heading_text:
                heading_text = stripped
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": heading_text}}]
                },
            })
        else:
            if current_paragraph:
                current_paragraph += "\n" + stripped
            else:
                current_paragraph = stripped

    flush_paragraph()
    return blocks


def _text_to_blocks(text: str, max_block_len: int = 2000) -> list[dict]:
    """将纯文本转换为 Notion paragraph blocks（自动处理超长截断）"""
    if not text or not text.strip():
        return []
    text = text.strip()
    blocks = []
    if len(text) <= max_block_len:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            },
        })
    else:
        for i in range(0, len(text), max_block_len):
            chunk = text[i:i + max_block_len]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                },
            })
    return blocks


def _make_heading_2(text: str) -> dict:
    """创建一个 Notion heading_2 block"""
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


# ── 深度背调报告锚点常量 ──
REPORT_ANCHOR_TEXT = "🤖 InterviewOS 深度背调报告"


def _get_all_page_blocks(page_id: str) -> list[dict]:
    """
    获取页面所有 blocks（自动处理分页）。
    
    返回 block 列表，每个 block 包含 id, type, 以及对应类型的内容。
    """
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    all_blocks = []
    start_cursor = None

    while True:
        url = f"{NOTION_BASE_URL}/blocks/{page_id}/children"
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        try:
            resp = _notion_request_with_retry(
                "GET", url, headers=headers, params=params, timeout=15,
            )
            data = resp.json()
            results = data.get("results", [])
            all_blocks.extend(results)

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
        except Exception as e:
            logger.error(f"获取页面 blocks 失败 (page_id={page_id}): {e}")
            break

    return all_blocks


def _find_anchor_block_index(blocks: list[dict]) -> int:
    """
    在 blocks 列表中查找锚点 heading_2 的位置。
    
    遍历 blocks，寻找文本内容完全匹配 REPORT_ANCHOR_TEXT 的 heading_2 block。
    如果找到，返回其在列表中的索引；否则返回 -1。
    """
    for idx, block in enumerate(blocks):
        block_type = block.get("type", "")
        if block_type != "heading_2":
            continue
        heading_2 = block.get("heading_2", {})
        rich_text = heading_2.get("rich_text", [])
        if not rich_text:
            continue
        text_content = "".join(
            rt.get("text", {}).get("content", "")
            for rt in rich_text
            if rt.get("type") == "text"
        )
        if text_content.strip() == REPORT_ANCHOR_TEXT:
            logger.info(f"找到锚点 block (index={idx}, block_id={block.get('id')})")
            return idx
    return -1


def _delete_blocks_from_index(page_id: str, blocks: list[dict], start_index: int) -> bool:
    """
    从指定索引开始，删除该索引及其之后的所有 blocks。
    
    参数:
        page_id: 页面 ID（仅用于日志）
        blocks: 完整的 blocks 列表
        start_index: 从此索引开始删除（含该索引）
    
    返回:
        全部删除成功返回 True，部分失败返回 False
    """
    if start_index < 0 or start_index >= len(blocks):
        logger.warning(f"无效的 start_index={start_index} (blocks 总数={len(blocks)})")
        return False

    target_blocks = blocks[start_index:]
    logger.info(f"准备删除 {len(target_blocks)} 个 blocks (从索引 {start_index} 开始)")

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    all_success = True
    for block in target_blocks:
        block_id = block.get("id")
        if not block_id:
            logger.warning(f"block 缺少 id，跳过: {block}")
            continue
        try:
            resp = _notion_request_with_retry(
                "DELETE",
                f"{NOTION_BASE_URL}/blocks/{block_id}",
                headers=headers,
                timeout=15,
            )
            if not resp.ok:
                logger.warning(f"删除 block 失败 (id={block_id}): {resp.status_code}")
                all_success = False
        except Exception as e:
            logger.error(f"删除 block 异常 (id={block_id}): {e}")
            all_success = False

    if all_success:
        logger.info(f"成功删除 {len(target_blocks)} 个 blocks")
    else:
        logger.warning(f"部分 blocks 删除失败，请检查日志")
    return all_success


def _append_blocks(page_id: str, new_blocks: list[dict]) -> bool:
    """
    将新 blocks 追加到页面底部。
    
    使用 PATCH /v1/blocks/{page_id}/children API。
    Notion 限制每次最多追加 100 个 block，超长时自动分批。
    """
    if not new_blocks:
        logger.info("没有新 blocks 需要追加")
        return True

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    # Notion API 限制：单次最多追加 100 个 block
    BATCH_SIZE = 100
    all_success = True

    for i in range(0, len(new_blocks), BATCH_SIZE):
        batch = new_blocks[i:i + BATCH_SIZE]
        try:
            resp = _notion_request_with_retry(
                "PATCH",
                f"{NOTION_BASE_URL}/blocks/{page_id}/children",
                headers=headers,
                json={"children": batch},
                timeout=30,
            )
            if not resp.ok:
                logger.error(f"追加 blocks 失败 (batch {i//BATCH_SIZE + 1}): {resp.status_code} {resp.text[:200]}")
                all_success = False
            else:
                logger.info(f"成功追加 batch {i//BATCH_SIZE + 1} ({len(batch)} blocks)")
        except Exception as e:
            logger.error(f"追加 blocks 异常 (batch {i//BATCH_SIZE + 1}): {e}")
            all_success = False

    return all_success


def replace_report_blocks(page_id: str, report_blocks: list[dict]) -> bool:
    """
    替换页面中的深度背调报告（原子化操作）。
    
    流程:
        1. 获取页面所有 blocks
        2. 查找锚点 heading_2（"🤖 InterviewOS 深度背调报告"）
        3. 如果找到锚点，删除锚点及其之后的所有 blocks
        4. 追加新的报告 blocks（含锚点标记）
    
    参数:
        page_id: Notion 页面 ID
        report_blocks: 新的报告 blocks 列表（必须已包含锚点 heading_2）
    
    返回:
        成功 True，失败 False
    """
    logger.info(f"开始替换页面报告 (page_id={page_id})")

    # 1. 获取所有 blocks
    all_blocks = _get_all_page_blocks(page_id)
    logger.info(f"页面共有 {len(all_blocks)} 个 blocks")

    # 2. 查找锚点
    anchor_index = _find_anchor_block_index(all_blocks)

    # 3. 如果找到锚点，删除旧报告
    if anchor_index >= 0:
        logger.info(f"发现旧报告 (从索引 {anchor_index} 开始)，执行删除...")
        delete_ok = _delete_blocks_from_index(page_id, all_blocks, anchor_index)
        if not delete_ok:
            logger.warning("旧报告删除不完全，继续尝试写入新报告...")
    else:
        logger.info("未找到旧报告锚点，直接追加新报告")

    # 4. 追加新报告
    append_ok = _append_blocks(page_id, report_blocks)
    if append_ok:
        logger.info(f"报告替换完成，共写入 {len(report_blocks)} 个 blocks")
    else:
        logger.error("报告写入失败")
    return append_ok


def _build_jd_children(
    job_description: str = "",
    job_requirements: str = "",
) -> list[dict]:
    """
    将结构化的职位描述和职位要求转换为 Notion children blocks。

    自动插入两个 Heading 2 标题：
      - 🎯 职位描述
      - 🛠️ 职位要求

    每个标题下方跟随对应的内容（paragraph blocks），
    超长文本自动按 2000 字符截断分段。

    参数:
        job_description: 职位描述文本
        job_requirements: 职位要求文本

    返回:
        Notion block 列表，可直接用于 create/update page 的 children 参数
    """
    blocks: list[dict] = []

    if job_description and job_description.strip():
        blocks.append(_make_heading_2("🎯 职位描述"))
        blocks.extend(_text_to_blocks(job_description))

    if job_requirements and job_requirements.strip():
        blocks.append(_make_heading_2("🛠️ 职位要求"))
        blocks.extend(_text_to_blocks(job_requirements))

    return blocks


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
    full_jd_body: str = "",
    job_description: str = "",
    job_requirements: str = "",
) -> Optional[str]:
    """
    在 Notion JobMonitor 数据库中创建新页面（使用 requests）。

    参数:
        full_jd_body: （已废弃，保留向后兼容）完整的 JD 全文
        job_description: 职位描述文本（结构化，推荐使用）
        job_requirements: 职位要求文本（结构化，推荐使用）

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

        body: dict[str, Any] = {
            "parent": {"database_id": NOTION_JOBS_DB},
            "properties": properties,
        }

        # ★ 核心：将结构化 JD 写入 Page Body（正文区域）
        # 优先使用结构化参数，向后兼容 full_jd_body
        if job_description or job_requirements:
            body["children"] = _build_jd_children(
                job_description=job_description,
                job_requirements=job_requirements,
            )
        elif full_jd_body:
            body["children"] = _build_jd_children(full_jd_body)

        resp = requests.post(
            f"{NOTION_BASE_URL}/pages",
            headers=_notion_headers(),
            json=body,
            timeout=30,  # 超长 JD 可能需要更长时间
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
    full_jd_body: str = "",
    job_description: str = "",
    job_requirements: str = "",
) -> bool:
    """
    更新 Notion 中已存在的岗位页面（使用 requests）。

    支持更新 Page Body（children），用于将残缺的 JD 替换为完整正文。

    参数:
        full_jd_body: （已废弃，保留向后兼容）完整的 JD 全文
        job_description: 职位描述文本（结构化，推荐使用）
        job_requirements: 职位要求文本（结构化，推荐使用）

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

        # ★ 核心：更新 Page Body（正文区域）
        # 先删除旧的 children，再写入新的结构化 JD
        if job_description or job_requirements or full_jd_body:
            # 先获取现有 page 的 children 并删除
            try:
                children_resp = requests.get(
                    f"{NOTION_BASE_URL}/blocks/{page_id}/children",
                    headers=_notion_headers(),
                    timeout=10,
                )
                if children_resp.ok:
                    existing_children = children_resp.json().get("results", [])
                    for child in existing_children:
                        child_id = child["id"]
                        requests.delete(
                            f"{NOTION_BASE_URL}/blocks/{child_id}",
                            headers=_notion_headers(),
                            timeout=10,
                        )
            except Exception:
                logger.debug("  删除旧 children 失败（可能没有旧内容），继续...")

        # 第一步：更新 properties（PATCH /pages/{page_id} 不支持同时更新 children）
        body: dict[str, Any] = {"properties": properties}
        resp = requests.patch(
            f"{NOTION_BASE_URL}/pages/{page_id}",
            headers=_notion_headers(),
            json=body,
            timeout=30,
        )
        resp.raise_for_status()

        # 第二步：写入新的结构化 JD（使用 append children API）
        if job_description or job_requirements or full_jd_body:
            if job_description or job_requirements:
                new_children = _build_jd_children(
                    job_description=job_description,
                    job_requirements=job_requirements,
                )
            elif full_jd_body:
                new_children = _build_jd_children(full_jd_body)

            if new_children:
                append_resp = requests.patch(
                    f"{NOTION_BASE_URL}/blocks/{page_id}/children",
                    headers=_notion_headers(),
                    json={"children": new_children},
                    timeout=30,
                )
                append_resp.raise_for_status()

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
    full_jd_body: str = "",
    job_description: str = "",
    job_requirements: str = "",
) -> bool:
    """
    同步单条岗位到 Notion（含去重逻辑）。

    流程:
        1. 按 URL 查询是否已存在
        2. 已存在 → 更新
        3. 不存在 → 新建（含完整 JD 写入 Page Body）

    参数:
        full_jd_body: （已废弃，保留向后兼容）完整的 JD 全文
        job_description: 职位描述文本（结构化，推荐使用）
        job_requirements: 职位要求文本（结构化，推荐使用）

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
            job_description=job_description,
            job_requirements=job_requirements,
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
            full_jd_body=full_jd_body,
            job_description=job_description,
            job_requirements=job_requirements,
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
            full_jd_body=job.get("full_jd_body", ""),
        )
        if ok:
            success += 1
        else:
            failed += 1

    total = len(jobs)
    logger.info("批量同步完成: 成功 %s / 失败 %s / 总计 %s", success, failed, total)
    return {"success": success, "failed": failed, "total": total}
