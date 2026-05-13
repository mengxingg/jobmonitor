"""
抓取核心模块（完全伪人化模式）

使用 Playwright connect_over_cdp 连接本地已打开的 Chrome（端口 9222），
扫描所有已打开的标签页，寻找 BOSS直聘列表页并提取数据。

核心原则：
  - 绝不执行任何导航/goto/刷新操作（只读）
  - 绝不关闭任何页面（只执行 browser.disconnect()）
  - 如果找不到 BOSS 页面，报错提示，绝不主动跳转
  - 不依赖网络空闲事件，改用硬等 + 多选择器保底
  - 不再使用 query_selector / query_selector_all（会被 browser-check 检测）
  - 改用 page.evaluate() 执行 JS 脚本直接从 window 环境提取数据
  - 提取前模拟真人滚动，触发懒加载并绕过反爬检测
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

from playwright.sync_api import sync_playwright

from config import CHROME_CDP_URL, is_blacklisted

logger = logging.getLogger(__name__)

# ── 数据结构 ──

@dataclass
class JobItem:
    """单条岗位数据"""
    title: str          # 岗位名称
    company: str        # 公司名称
    salary: str         # 薪资范围（原始文本）
    location: str       # 工作地点
    url: str            # 岗位详情页链接
    platform: str       # 来源平台，如 "BOSS直聘"


# ── 提取数据的 JS 脚本 ──

EXTRACT_JOBS_JS = """
() => {
    // 尝试多种可能的卡片容器选择器
    const containers = [
        document.querySelector('.job-list-box'),
        document.querySelector('[class*="job-list"]'),
        document.querySelector('[class*="geek-list"]'),
        document.querySelector('.search-job-result'),
        document.querySelector('[class*="search-job"]'),
    ];
    const container = containers.find(c => c !== null);
    if (!container) {
        // 最后尝试：直接找所有可能的卡片元素
        const allCards = document.querySelectorAll(
            '.job-card-wrapper, [class*="job-card"], .job-list-box > li, [class*="geek-list"] > li'
        );
        if (allCards.length === 0) return [];
        return Array.from(allCards).map(card => _extractFromElement(card));
    }

    // 从容器中找所有卡片
    const cards = container.querySelectorAll(
        ':scope > li, :scope > div[class*="card"], :scope > a[class*="card"], .job-card-wrapper'
    );
    if (cards.length === 0) {
        // 降级：直接找所有可能的卡片
        const fallback = container.querySelectorAll(
            '.job-card-wrapper, [class*="job-card"], li[class*="job"]'
        );
        return Array.from(fallback).map(card => _extractFromElement(card));
    }
    return Array.from(cards).map(card => _extractFromElement(card));

    // ----- 内部提取函数 -----
    function _extractFromElement(el) {
        const result = { title: '', company: '', salary: '', location: '', url: '' };

        // 1. 标题
        const titleEl = el.querySelector(
            '.job-name, a.job-name, span.job-name, h3.name, [class*="job-name"], [class*="job-title"], .job_title'
        );
        if (titleEl) result.title = titleEl.innerText.trim();

        // 2. 公司名
        const companyEl = el.querySelector(
            '.company-name, a.company-name, span.company-name, h3.company, [class*="company-name"], .company_text, [class*="company"]'
        );
        if (companyEl) result.company = companyEl.innerText.trim();

        // 3. 薪资
        const salaryEl = el.querySelector(
            '.salary, span.salary, .job-salary, [class*="salary"], .job_price, .price'
        );
        if (salaryEl) result.salary = salaryEl.innerText.trim();

        // 4. 地点
        const locationEl = el.querySelector(
            '.job-area, span.job-area, .location, [class*="job-area"], .job_location, .job-address'
        );
        if (locationEl) result.location = locationEl.innerText.trim();

        // 5. 链接
        const linkEl = el.tagName === 'A' ? el : el.querySelector('a[href*="job_detail"], a[class*="job-card"], a[href*="geek"]');
        if (linkEl) {
            let href = linkEl.getAttribute('href') || '';
            if (href) {
                result.url = href.startsWith('http') ? href : 'https://www.zhipin.com' + href;
            }
        }

        return result;
    }
}
"""


# ── 抓取函数 ──

def scrape_boss_list() -> list[JobItem]:
    """
    扫描所有已打开的标签页，寻找 BOSS直聘列表页并提取数据。

    查找策略（满足任一条件即可）：
      条件 A：URL 包含 zhipin.com/web/geek/jobs（列表页路径）
      条件 B：页面内存在任意卡片选择器匹配的元素（通过 JS 检测）

    如果完全找不到任何 BOSS 页面，报错提示，绝不主动跳转。

    返回:
        JobItem 列表（已过滤黑名单）
    """
    all_jobs: list[JobItem] = []

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(CHROME_CDP_URL)
        logger.info("已连接到 Chrome CDP: %s", CHROME_CDP_URL)

        contexts = browser.contexts
        if not contexts:
            logger.error("未找到浏览器上下文，请确保 Chrome 有打开的标签页")
            return []
        context = contexts[0]

        pages = context.pages
        if not pages:
            logger.error("未找到可用的标签页")
            return []

        logger.info("共发现 %s 个标签页:", len(pages))

        # ── 扫描所有页面，寻找 BOSS 列表页 ──
        target_page = None
        for i, p in enumerate(pages):
            url = ""
            try:
                url = p.url
            except Exception:
                url = "<无法获取 URL>"
            logger.info("  标签页 [%s]: %s", i, url)

            if url == "<无法获取 URL>":
                continue

            # 条件 A：URL 匹配 BOSS 列表页路径
            if "zhipin.com/web/geek/jobs" in url:
                logger.info("  → 条件 A 命中（URL 匹配列表页路径），锁定此页面")
                target_page = p
                break

            # 条件 B：通过 JS 检测页面内是否存在卡片元素
            try:
                has_cards = p.evaluate("""
                    () => {
                        const sel = '.job-card-wrapper, [class*="job-card"], .job-list-box li';
                        return document.querySelector(sel) !== null;
                    }
                """)
                if has_cards:
                    logger.info("  → 条件 B 命中（JS 检测到卡片元素），锁定此页面")
                    target_page = p
                    break
            except Exception:
                continue

        if not target_page:
            logger.warning(
                "未找到 BOSS 直聘列表页。\n"
                "当前所有标签页 URL 如上所示。\n"
                "请确认：\n"
                "  1. Chrome 已打开 BOSS 直聘列表页（URL 应包含 zhipin.com/web/geek/jobs）\n"
                "  2. 页面已完全加载，列表已渲染\n"
                "  3. 如页面有验证码/登录拦截，请手动处理后再试\n"
                "提示：脚本不会主动跳转，请手动在浏览器中导航到目标页面。"
            )
            return []

        # ── 置顶目标页面 ──
        try:
            target_page.bring_to_front()
        except Exception:
            pass

        # ── 硬等 2 秒让页面渲染 ──
        logger.info("硬等 2 秒等待页面渲染...")
        target_page.wait_for_timeout(2000)

        # ── 模拟真人滚动（触发懒加载 + 绕过反爬检测） ──
        logger.info("模拟真人滚动...")
        try:
            # 向下滚动 500 像素
            target_page.mouse.wheel(0, 500)
            time.sleep(1)
            # 再滚回来
            target_page.mouse.wheel(0, -500)
            time.sleep(0.5)
            logger.info("滚动完成")
        except Exception as e:
            logger.warning("模拟滚动失败（不影响后续提取）: %s", e)

        # ── 再等 1 秒让懒加载数据到达 ──
        target_page.wait_for_timeout(1000)

        # ── 通过 JS 提取数据（完全绕过 query_selector） ──
        logger.info("通过 page.evaluate() 执行 JS 提取数据...")
        try:
            raw_jobs = target_page.evaluate(EXTRACT_JOBS_JS)
        except Exception as e:
            logger.error("JS 提取失败: %s", e)
            # 输出 HTML 片段用于调试
            html_snippet = ""
            try:
                html_snippet = target_page.content()[:1000]
            except Exception:
                pass
            logger.warning(
                "目标页面 JS 提取失败。\n"
                "  URL: %s\n  Title: %s\n"
                "  HTML 前 1000 字符:\n%s",
                target_page.url,
                _safe_title(target_page),
                html_snippet,
            )
            return []

        if not raw_jobs or not isinstance(raw_jobs, list):
            logger.warning("JS 提取结果为空或格式异常: %s", raw_jobs)
            return []

        logger.info("JS 提取到 %s 条原始数据", len(raw_jobs))

        # ── 转换为 JobItem ──
        for idx, raw in enumerate(raw_jobs, 1):
            try:
                if not isinstance(raw, dict):
                    continue
                title = (raw.get("title") or "").strip()
                company = (raw.get("company") or "").strip()
                if not title and not company:
                    continue

                job = JobItem(
                    title=title or "未知岗位",
                    company=company or "未知公司",
                    salary=(raw.get("salary") or "").strip(),
                    location=(raw.get("location") or "").strip(),
                    url=(raw.get("url") or "").strip(),
                    platform="BOSS直聘",
                )

                if is_blacklisted(job.company):
                    logger.info("黑名单跳过 [%s/%s]: %s - %s", idx, len(raw_jobs), job.company, job.title)
                    continue
                all_jobs.append(job)
            except Exception as e:
                logger.debug("解析第 %s 条数据失败: %s", idx, e)
                continue

    finally:
        try:
            browser.disconnect()
            logger.info("已断开 CDP 连接")
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass

    logger.info("抓取完成，共获取 %s 个岗位（已过滤黑名单）", len(all_jobs))
    return all_jobs


# ── 辅助函数 ──

def _safe_title(page) -> str:
    """安全获取页面标题"""
    try:
        return page.title()
    except Exception:
        return "<无法获取标题>"


# ── 命令行测试 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    jobs = scrape_boss_list()
    for j in jobs:
        print(json.dumps(asdict(j), ensure_ascii=False, indent=2))
