"""
openclaw_bridge.py
将 OpenClaw 抓取的岗位数据对接到已有的 ai_matcher + notion_sync 管道

使用方法：
    conda activate job_env
    python openclaw_bridge.py

数据流：
    OpenClaw 输出 JSON → 本脚本读取 → ai_matcher.evaluate_job() 评分 → notion_sync 写入 Notion

核心功能：
    1. 读取 OpenClaw Deep Crawl 输出的 JSON（含 full_jd + requirements）
    2. 调用 AI Matcher 进行 5 维评分
    3. 双重写入策略：
       - JD Summary 字段：写入完整 JD 前 1800 字（供表格预览）
       - Page Body（children）：写入毫发无损的完整 JD 全文（点击卡片可查看全貌）
    4. 写入 Notion 前通过 API 查询 URL 去重，已存在则跳过
"""

import json
import os
import sys
import re
import time
import random
from datetime import datetime

# 导入已有管道模块
from ai_matcher import evaluate_job
from notion_sync import sync_job, find_existing_by_url


# === 配置 ===
OPENCLAW_OUTPUT = os.path.join(os.path.dirname(__file__), "data", "openclaw_jobs.json")
PROCESSED_LOG = os.path.join(os.path.dirname(__file__), "data", "openclaw_processed.json")

# 来源标签：所有通过 OpenClaw 抓取的岗位都标记为"官网"
SOURCE_LABEL = "官网"

# JD Summary 最大字符数（Notion rich_text 字段安全上限）
JD_SUMMARY_MAX_CHARS = 1800

# ── 产品经理关键词过滤（写入 Notion 前的二次校验） ──
PM_KEYWORDS = ["产品经理", "产品", "PM", "产品运营", "产品策划", "产品专家", "产品负责人"]
EXCLUDE_KEYWORDS = ["算法", "工程师", "开发", "架构师", "测试", "运维", "前端", "后端", "全栈",
                    "数据挖掘", "NLP", "CV", "机器学习", "深度学习", "研究员", "科学家",
                    "设计", "UI", "UX", "视觉", "交互", "市场", "销售",
                    "HR", "人力", "行政", "财务", "法务"]


def is_pm_related(title: str) -> bool:
    """判断岗位标题是否与产品经理相关（写入 Notion 前的二次校验）"""
    if not title:
        return False
    for kw in PM_KEYWORDS:
        if kw in title:
            return True
    for ek in EXCLUDE_KEYWORDS:
        if ek in title:
            return False
    return False


def load_openclaw_jobs(filepath: str) -> list[dict]:
    """读取 OpenClaw 输出的 JSON 文件"""
    if not os.path.exists(filepath):
        print(f"[ERROR] 文件不存在: {filepath}")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    print(f"[INFO] 读取到 {len(jobs)} 条岗位数据")
    return jobs


def load_processed_urls() -> set:
    """加载已处理过的 URL，避免重复写入 Notion"""
    if not os.path.exists(PROCESSED_LOG):
        return set()

    with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("urls", []))


def save_processed_urls(urls: set):
    """保存已处理的 URL 列表"""
    with open(PROCESSED_LOG, "w", encoding="utf-8") as f:
        json.dump({
            "urls": list(urls),
            "last_updated": datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)


def normalize_job(job: dict) -> dict:
    """
    标准化单条 OpenClaw 岗位数据，确保字段名与下游管道兼容。

    核心逻辑：
        1. 强制打上 platform = '官网' 的来源标签
        2. 将 OpenClaw 的字段名映射为下游 notion_sync 期望的字段名
        3. 结构化存储 job_description + job_requirements（v3.0 新增）
        4. 向后兼容：仍生成 full_jd_body 供旧版调用方使用
        5. 补充缺失的默认值
    """
    normalized = {}

    # ── 字段映射：OpenClaw 原始字段 → 下游标准字段 ──
    normalized["title"] = job.get("title") or job.get("job_name") or job.get("jobName") or ""
    normalized["company"] = job.get("company") or job.get("brandName") or ""
    normalized["url"] = job.get("url") or ""
    normalized["salary_range"] = job.get("salary") or job.get("salaryDesc") or "面议"
    normalized["location"] = job.get("location") or job.get("city") or job.get("cityName") or ""

    # ── ★ v3.0 结构化字段：直接从抓取结果中读取 ──
    # bytedance_visual_crawler.py v3.0 输出 job_description + job_requirements
    # 旧版数据仍使用 full_jd / requirements 回退
    job_description = job.get("job_description") or job.get("full_jd") or job.get("description") or ""
    raw_requirements = job.get("job_requirements") or job.get("requirements") or ""

    # 清理职位要求中的导航文本杂质
    import re
    positions = [m.start() for m in re.finditer(r'职位\s*ID[：:]', raw_requirements)]
    if len(positions) >= 2:
        raw_requirements = raw_requirements[:positions[1]].strip()
    raw_requirements = re.sub(r'\n(北京|上海|深圳|杭州|广州|成都)\s+(产品|运营|技术|设计|市场|销售|职能|战略)(\s*[-–—]\s*\S+)?$', '', raw_requirements)
    for marker in ["联系我们", "相关网站", "分享到", "推荐职位"]:
        idx = raw_requirements.find(marker)
        if idx > 0:
            raw_requirements = raw_requirements[:idx].strip()
            break

    normalized["job_description"] = job_description
    normalized["job_requirements"] = raw_requirements

    # ── 向后兼容：保留旧字段 ──
    normalized["full_jd"] = job_description
    normalized["requirements"] = raw_requirements

    # ── ★ 核心：拼接完整 JD 原文（向后兼容） ──
    # 旧版 _build_jd_children 仍可接收 full_jd_body
    full_text_parts = []
    if job_description:
        full_text_parts.append("【职位描述】\n" + job_description)
    if raw_requirements:
        full_text_parts.append("\n【任职要求】\n" + raw_requirements)
    normalized["full_jd_body"] = "\n\n".join(full_text_parts)

    # ── ★ JD Summary：完整 JD 前 1800 字（供表格预览） ──
    raw_jd_summary = job.get("jd_summary") or ""
    if not raw_jd_summary and normalized["full_jd_body"]:
        raw_jd_summary = normalized["full_jd_body"][:JD_SUMMARY_MAX_CHARS]
    normalized["jd_summary"] = raw_jd_summary

    # ── 强制打上来源标签 ──
    normalized["platform"] = SOURCE_LABEL

    # ── 远程/混合/现场 ──
    normalized["remote"] = job.get("remote", "")

    return normalized


def check_notion_duplicate(url: str, max_retries: int = 3) -> bool:
    """
    通过 Notion API 查询 URL 是否已存在于数据库中。
    这是写入前的第二道去重防线（第一道是本地 processed_urls 缓存）。

    参数:
        url: 岗位 URL
        max_retries: 最大重试次数（默认 3 次，含指数退避）

    返回:
        True 表示已存在（重复），False 表示不存在（可写入）
    """
    if not url:
        return False

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            existing_id = find_existing_by_url(url)
            return existing_id is not None
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print(f"  [RETRY] Notion 去重查询失败 (第{attempt}次, {wait:.1f}秒后重试): {e}")
                time.sleep(wait)
            else:
                print(f"  [WARN] Notion 去重查询失败 (已重试{max_retries}次): {url}: {e}")

    return False


def process_jobs(jobs: list[dict], force_update: bool = False) -> list[dict]:
    """
    对每条岗位进行标准化 → AI 评分 → 准备写入 Notion

    参数:
        force_update: 如果为 True，跳过本地去重缓存，强制更新所有数据（含已存在的 Notion 页面）

    返回标准化后的岗位列表，每条包含 notion_sync.sync_job() 所需的所有字段。
    """
    processed_urls = load_processed_urls() if not force_update else set()
    results = []
    skipped = 0
    errors = 0

    for i, job in enumerate(jobs):
        # 第一步：标准化字段
        normalized = normalize_job(job)
        url = normalized["url"]

        # 跳过无 URL 的数据
        if not url:
            print(f"  [SKIP] 第 {i+1} 条数据缺少 URL，跳过")
            skipped += 1
            continue

        # ★ 产品经理二次校验：非产品岗直接跳过，不写入 Notion
        if not is_pm_related(normalized["title"]):
            print(f"  [SKIP] 非产品经理岗位，跳过写入 Notion: {normalized['title']}")
            skipped += 1
            continue

        # 第一道去重防线：本地 processed_urls 缓存（仅在非 force 模式下启用）
        if not force_update and url in processed_urls:
            print(f"  [SKIP] 本地缓存已处理，跳过: {normalized['title']}")
            skipped += 1
            continue

        # 第二道去重防线：Notion API 实时查询（仅在非 force 模式下启用）
        if not force_update and check_notion_duplicate(url):
            print(f"  [SKIP] Notion 已存在，跳过写入: {normalized['title']}")
            processed_urls.add(url)
            skipped += 1
            continue

        print(f"[{i+1}/{len(jobs)}] 评分: {normalized['title']} @ {normalized['company']}")

        try:
            # 调用 AI Matcher 评分
            score_result = evaluate_job(
                title=normalized["title"],
                company=normalized["company"],
                salary=normalized["salary_range"],
                location=normalized["location"],
                platform=normalized["platform"],
                jd_summary=normalized["jd_summary"],
                full_jd=normalized["full_jd"],
                requirements=normalized["requirements"],
            )

            # 合并评分结果
            normalized["match_score"] = score_result.get("score", 0)
            normalized["match_reasons"] = score_result.get("match_reasons", [])
            normalized["mismatch_reasons"] = score_result.get("mismatch_reasons", [])
            normalized["notes"] = score_result.get("summary", "")

            normalized["discovered_date"] = datetime.now().strftime("%Y-%m-%d")
            normalized["status"] = "新发现"

            # 设置优先级
            score = normalized["match_score"]
            if score >= 80:
                normalized["priority"] = "高"
            elif score >= 60:
                normalized["priority"] = "中"
            else:
                normalized["priority"] = "低"

            results.append(normalized)
            processed_urls.add(url)

        except Exception as e:
            print(f"  [ERROR] 评分失败: {e}")
            errors += 1

    # 保存已处理的 URL
    if not force_update:
        save_processed_urls(processed_urls)

    print(f"\n[结果] 新增 {len(results)} 条 | 跳过已处理 {skipped} 条 | 失败 {errors} 条")
    return results


def main():
    print("=" * 50)
    print("OpenClaw → AI Matcher → Notion 桥接脚本")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"来源标签: {SOURCE_LABEL}")

    # 检测是否强制更新模式
    force_update = os.environ.get("FORCE_UPDATE", "").strip() == "1"
    if force_update:
        print("🔄 强制更新模式: 跳过本地去重缓存，强制更新所有已存在的 Notion 页面（含 Page Body）")
    print("=" * 50)

    # 1. 读取 OpenClaw 输出
    jobs = load_openclaw_jobs(OPENCLAW_OUTPUT)
    if not jobs:
        print("[完成] 没有新数据需要处理")
        return

    # 2. 标准化 + AI 评分（force_update 时跳过本地去重）
    scored_jobs = process_jobs(jobs, force_update=force_update)
    if not scored_jobs:
        print("[完成] 没有新岗位需要写入")
        return

    # 3. 写入 Notion（含完整 JD 写入 Page Body）
    print(f"\n[INFO] 开始写入 Notion ({len(scored_jobs)} 条)...")
    success = 0
    failed = 0
    for job in scored_jobs:
        try:
            # 打印 JD 长度信息
            jd_len = len(job.get("full_jd_body", ""))
            summary_len = len(job.get("jd_summary", ""))
            print(f"  → {job['title']} (JD: {jd_len}字, 摘要: {summary_len}字)")

            ok = sync_job(
                title=job["title"],
                company=job["company"],
                platform=job["platform"],
                url=job["url"],
                location=job["location"],
                remote=job.get("remote", ""),
                salary_range=job["salary_range"],
                jd_summary=job.get("jd_summary", ""),
                match_score=job["match_score"],
                match_reasons=job.get("match_reasons"),
                mismatch_reasons=job.get("mismatch_reasons"),
                status=job["status"],
                priority=job["priority"],
                discovered_date=job["discovered_date"],
                notes=job.get("notes", ""),
                # ★ v3.0：传递结构化字段，Notion 正文将自动生成带 emoji 标题的分段排版
                job_description=job.get("job_description", ""),
                job_requirements=job.get("job_requirements", ""),
                # 向后兼容
                full_jd_body=job.get("full_jd_body", ""),
            )
            if ok:
                success += 1
                print(f"  ✓ {job['title']} @ {job['company']} (得分: {job['match_score']})")
            else:
                failed += 1
                print(f"  ✗ {job['title']} @ {job['company']} - 写入失败")
        except Exception as e:
            failed += 1
            print(f"  ✗ {job['title']} @ {job['company']} - 异常: {e}")

    print(f"\n[完成] 成功 {success} 条 / 失败 {failed} 条 / 总计 {len(scored_jobs)} 条")


if __name__ == "__main__":
    main()
