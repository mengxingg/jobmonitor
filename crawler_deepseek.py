#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
crawler_deepseek.py — DeepSeek (Moka 系统) 精准爬虫

使用 Playwright 访问带 keyword 参数的精准搜索 URL，
只抓取「产品经理」相关岗位，提取职位描述和任职要求。

URL:
  https://app.mokahr.com/social-recruitment/high-flyer/140576#/jobs?keyword=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&page=1&anchorName=jobsList

Moka 系统特点：
  - SPA hash 路由，URL 变化时页面不自动刷新
  - 列表页岗位卡片在 .job-list 或 .position-list 容器中
  - 详情页 JD 内容在 .job-content 或 .job-detail 容器中
  - 翻页通过点击「下一页」按钮

用法:
  conda run -n job_env python crawler_deepseek.py
"""

import sys, io, json, time, logging, re, random, subprocess, os
from pathlib import Path
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright, Page

# ── UTF-8 输出 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("deepseek_crawler")

# ── 配置 ──
TARGET_URL = (
    "https://app.mokahr.com/social-recruitment/high-flyer/140576"
    "#/jobs?keyword=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&page=1&anchorName=jobsList"
)
COMPANY_NAME = "DeepSeek"
COMPANY_LOCATION = "杭州"
MAX_PAGES = 3
MIN_WAIT = 2.0
MAX_WAIT = 3.0
PAGE_LOAD_TIMEOUT = 60000
NAVIGATE_TIMEOUT = 30000
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "openclaw_jobs.json"
BRIDGE_SCRIPT = Path(__file__).parent / "openclaw_bridge.py"


def random_sleep(label: str = ""):
    t = random.uniform(MIN_WAIT, MAX_WAIT)
    if label:
        logger.info(f"⏳ {label}，等待 {t:.1f} 秒...")
    else:
        logger.info(f"⏳ 等待 {t:.1f} 秒...")
    time.sleep(t)


def init_browser() -> tuple:
    """启动 Playwright 有界面浏览器"""
    logger.info("🚀 启动 Playwright 浏览器 (headless=False)...")
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-infobars",
            "--hide-crash-restore-bubble",
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1400, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    """)
    page = context.new_page()
    page.set_default_timeout(PAGE_LOAD_TIMEOUT)
    logger.info("✅ 浏览器已启动 (viewport=1400x900)")
    return p, browser, context, page


def wait_for_job_list(page: Page) -> bool:
    """
    等待 Moka 系统的岗位列表渲染。
    Moka 是 SPA，URL hash 变化时页面不会自动刷新，
    需要等待 .job-list 或 .position-list 容器出现。
    """
    selectors = [
        ".job-list",
        ".position-list",
        ".list-content",
        "a[href*='/position/']",
        "a[href*='/job/']",
        "[class*='job-list']",
        "[class*='position-list']",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=15000)
            logger.info(f"✅ 列表已渲染 (selector={selector})")
            time.sleep(2)  # 额外等待确保所有卡片加载完成
            return True
        except Exception:
            continue
    logger.warning("⚠️ 岗位列表选择器全部超时，尝试 JS 检测...")
    # JS 兜底：检查是否有岗位卡片
    try:
        has_cards = page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    '.job-card, .position-item, .list-item, '
                    '[class*="job-card"], [class*="position-item"]'
                );
                return cards.length > 0;
            }
        """)
        if has_cards:
            logger.info("✅ JS 检测到岗位卡片存在")
            return True
    except Exception:
        pass
    return False


# ── 产品经理关键词过滤列表 ──
PM_KEYWORDS = ["产品经理", "产品", "PM", "产品运营", "产品策划", "产品专家", "产品负责人"]


def is_pm_related(title: str) -> bool:
    """判断岗位标题是否与产品经理相关"""
    if not title:
        return False
    title_lower = title.lower()
    for kw in PM_KEYWORDS:
        if kw in title:
            return True
    # 排除明显不相关的岗位（包含这些关键词的就不是产品岗）
    exclude_keywords = ["算法", "工程师", "开发", "架构师", "测试", "运维", "前端", "后端", "全栈",
                        "数据挖掘", "NLP", "CV", "机器学习", "深度学习", "研究员", "科学家",
                        "设计", "UI", "UX", "视觉", "交互", "市场", "销售", "运营（非产品运营）",
                        "HR", "人力", "行政", "财务", "法务"]
    for ek in exclude_keywords:
        if ek in title:
            return False
    return False


def collect_job_links(page: Page) -> list[dict]:
    """
    从 Moka 列表页提取所有岗位链接。
    只保留标题包含"产品经理"相关关键词的岗位。
    返回 [{"title": "...", "url": "..."}, ...]
    """
    jobs = []

    # 方案1: 通过 <a> 标签提取
    links = page.eval_on_selector_all(
        "a[href*='/position/'], a[href*='/job/']",
        """els => els.map(el => ({
            url: el.href,
            title: (el.querySelector('.job-title, .position-name, .name, h3, h4, .title')
                    || el).innerText.trim()
        }))"""
    )
    if links and len(links) > 0:
        valid = [j for j in links if j["url"] and j["title"]]
        if valid:
            # ★ 只保留产品经理相关岗位
            pm_jobs = [j for j in valid if is_pm_related(j["title"])]
            logger.info(f"📋 通过 <a> 标签提取到 {len(valid)} 个，其中产品经理相关 {len(pm_jobs)} 个")
            for j in valid:
                if j not in pm_jobs:
                    logger.info(f"  🚫 过滤非产品岗: {j['title']}")
            return pm_jobs

    # 方案2: JS 兜底提取
    jobs = page.evaluate("""
        () => {
            const results = [];
            const cards = document.querySelectorAll(
                '.job-card, .position-item, .list-item, '
                '[class*="job-card"], [class*="position-item"], '
                'li[class*="item"]'
            );
            cards.forEach(card => {
                const link = card.querySelector('a');
                const url = link ? link.href : '';
                const title = (card.querySelector(
                    '.job-title, .position-name, .name, h3, h4, .title, '
                    '[class*="title"], [class*="name"]'
                ) || card).innerText.trim().split('\\n')[0].trim();
                if (url && title) {
                    results.push({ url, title });
                }
            });
            if (results.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    if (a.href && (a.href.includes('/position/') || a.href.includes('/job/'))) {
                        results.push({
                            url: a.href,
                            title: a.innerText.trim().split('\\n')[0].trim()
                        });
                    }
                });
            }
            return results;
        }
    """)

    if jobs and len(jobs) > 0:
        # ★ 只保留产品经理相关岗位
        pm_jobs = [j for j in jobs if is_pm_related(j["title"])]
        logger.info(f"📋 通过 JS 提取到 {len(jobs)} 个，其中产品经理相关 {len(pm_jobs)} 个")
        for j in jobs:
            if j not in pm_jobs:
                logger.info(f"  🚫 过滤非产品岗: {j['title']}")
        return pm_jobs
    else:
        logger.warning("⚠️ 未提取到任何岗位链接！")

    return jobs


def click_next_page(page: Page) -> bool:
    """Moka 系统的翻页按钮点击"""
    next_selectors = [
        "button:has-text('下一页')",
        "a:has-text('下一页')",
        ".pagination .next:not(.disabled)",
        ".pagination button:last-child:not([disabled])",
        "li.next:not(.disabled) a",
        "li.next:not(.disabled) button",
        "[class*='next']:not([class*='disabled'])",
        "button[class*='next']:not([disabled])",
        "[aria-label='Next']",
        "[aria-label='下一页']",
        ".page-next:not(.disabled)",
        ".el-pagination .btn-next:not(.disabled)",
    ]

    for selector in next_selectors:
        try:
            btn = page.query_selector(selector)
            if btn is None:
                continue

            is_disabled = btn.get_attribute("disabled") is not None
            class_attr = btn.get_attribute("class") or ""
            if is_disabled or "disabled" in class_attr:
                logger.info(f"🔚 『下一页』按钮已禁用 (selector={selector})")
                return False

            is_visible = btn.is_visible()
            if not is_visible:
                continue

            btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            logger.info(f"👉 点击『下一页』按钮 (selector={selector})")
            btn.click()
            return True
        except Exception as e:
            logger.debug(f"  选择器 '{selector}' 失败: {e}")
            continue

    # JS 兜底
    try:
        result = page.evaluate("""
            () => {
                const allElements = document.querySelectorAll('button, a, li, span, div');
                for (const el of allElements) {
                    const text = el.innerText.trim();
                    if (text === '下一页' || text === 'Next' || text === 'next') {
                        if (el.disabled || el.classList.contains('disabled')) return 'disabled';
                        el.click();
                        return 'clicked';
                    }
                }
                return 'not_found';
            }
        """)
        if result == "clicked":
            logger.info("👉 通过 JS 兜底点击了『下一页』")
            return True
        elif result == "disabled":
            logger.info("🔚 『下一页』按钮已禁用 (JS 检测)")
            return False
        else:
            logger.info("🔚 未找到『下一页』按钮")
            return False
    except Exception as e:
        logger.warning(f"⚠️ JS 兜底点击失败: {e}")
        return False


def extract_job_detail(page: Page, url: str, title: str) -> Optional[dict]:
    """
    从 Moka 详情页提取职位描述和要求。
    """
    logger.info(f"🔍 进入详情页: {title}")
    try:
        page.goto(url, timeout=NAVIGATE_TIMEOUT, wait_until="domcontentloaded")
        time.sleep(3)

        # 等待 JD 内容加载
        try:
            page.wait_for_selector(
                "[class*='job-description'], .apply__content, "
                ".job-G9ROFuiAF_, .main-content-Xjvz9jbcWO, "
                "[class*='main-content'], .job-detail",
                timeout=15000,
            )
            time.sleep(2)
        except Exception:
            logger.warning("  ⚠️ JD 容器等待超时，尝试直接提取...")

        extracted = page.evaluate("""
            () => {
                const containerSelectors = [
                    '[class*="job-description"]',
                    '.apply__content',
                    '.main-content-Xjvz9jbcWO',
                    '[class*="main-content"]',
                    '.job-detail',
                    '.position-detail',
                    '.jd-content',
                    '.detail-content',
                    '.content-wrapper',
                    'main',
                    'article',
                ];
                let container = null;
                for (const sel of containerSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 100) {
                        container = el;
                        break;
                    }
                }
                if (!container) {
                    const candidates = document.querySelectorAll('div[class], section[class], article');
                    let maxLen = 0;
                    for (const el of candidates) {
                        const len = el.innerText.trim().length;
                        if (len > maxLen && len < 50000) {
                            maxLen = len;
                            container = el;
                        }
                    }
                }
                if (!container) {
                    return { job_description: '', job_requirements: '', page_text: document.body.innerText };
                }

                const page_text = container.innerText;

                let job_description = '';
                let job_requirements = '';

                // 提取"岗位职责"到"任职要求"之间的内容
                const descMatch = page_text.match(
                    /(?:岗位职责|职位描述|工作职责|岗位描述)[\\\\s\\\\n]*([\\\\s\\\\S]*?)(?=\\\\n\\\\s*(?:任职要求|职位要求|岗位要求|任职资格|加分项|我们希望你|关于你|职位信息|$))/
                );
                if (descMatch) {
                    job_description = descMatch[1].trim();
                }

                // 提取"任职要求"到"加分项"之间的内容
                const reqMatch = page_text.match(
                    /(?:任职要求|职位要求|岗位要求|任职资格)[\\\\s\\\\n]*([\\\\s\\\\S]*?)(?=\\\\n\\\\s*(?:加分项|职位信息|我们希望你|关于你|最新职位|$))/
                );
                if (reqMatch) {
                    job_requirements = reqMatch[1].trim();
                }

                if (!job_description && !job_requirements) {
                    const allMatch = page_text.match(
                        /(?:职位描述|岗位职责|工作职责)[\\\\s\\\\n]*([\\\\s\\\\S]*?)(?=职位信息|最新职位|$)/i
                    );
                    if (allMatch) {
                        job_description = allMatch[1].trim();
                    }
                }

                if (!job_description && !job_requirements) {
                    job_description = page_text.slice(0, 3000);
                }

                return { job_description, job_requirements, page_text };
            }
        """)

        job_description = extracted.get("job_description", "")
        job_requirements = extracted.get("job_requirements", "")
        page_text = extracted.get("page_text", "")

        job_description = re.sub(r"[ \t]+", " ", job_description).strip()
        job_requirements = re.sub(r"[ \t]+", " ", job_requirements).strip()

        logger.info(f"  ✅ 提取完成: 描述 {len(job_description)} 字符, 要求 {len(job_requirements)} 字符")

        return {
            "title": title,
            "company": COMPANY_NAME,
            "salary": "面议",
            "location": COMPANY_LOCATION,
            "url": url,
            "job_description": job_description,
            "job_requirements": job_requirements,
            "full_jd": job_description,
            "requirements": job_requirements,
        }

    except Exception as e:
        logger.error(f"  ❌ 详情页提取失败: {e}")
        return None


def crawl() -> list[dict]:
    """主抓取流程"""
    p = browser = context = page = None
    all_job_links = []
    all_job_details = []

    try:
        p, browser, context, page = init_browser()

        logger.info(f"🌐 访问 DeepSeek 招聘页面 (精准搜索: 产品经理)...")
        logger.info(f"   URL: {TARGET_URL}")
        page.goto(TARGET_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        logger.info("✅ 页面加载完成")

        # ★ 关键：等待 Moka SPA 渲染岗位列表
        random_sleep("等待页面渲染稳定")
        if not wait_for_job_list(page):
            logger.warning("⚠️ 岗位列表未渲染，尝试继续...")

        # 循环翻页
        for page_num in range(1, MAX_PAGES + 1):
            logger.info(f"\n📄 第 {page_num}/{MAX_PAGES} 页")

            page_jobs = collect_job_links(page)
            logger.info(f"📌 当前页获取到 {len(page_jobs)} 个岗位")

            existing_urls = {j["url"] for j in all_job_links}
            new_jobs = [j for j in page_jobs if j["url"] not in existing_urls]
            if new_jobs:
                all_job_links.extend(new_jobs)
                logger.info(f"✨ 新增 {len(new_jobs)} 个，累计 {len(all_job_links)} 个")
            else:
                logger.info(f"💡 当前页无新岗位，累计 {len(all_job_links)} 个")

            if page_num < MAX_PAGES:
                random_sleep("翻页前模拟浏览")
                clicked = click_next_page(page)
                if not clicked:
                    logger.info("🏁 已到达最后一页，停止翻页")
                    break
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(2)
            else:
                logger.info(f"✅ 已达到最大翻页数 {MAX_PAGES}")

        logger.info(f"\n📊 共收集到 {len(all_job_links)} 个岗位 URL")

        # 逐个访问详情页
        logger.info(f"\n🔎 开始深度抓取岗位详情 ({len(all_job_links)} 个)")
        for idx, job in enumerate(all_job_links, 1):
            logger.info(f"\n[{idx}/{len(all_job_links)}] {job['title']}")
            detail = extract_job_detail(page, job["url"], job["title"])
            if detail:
                all_job_details.append(detail)
                logger.info(f"  ✅ [{idx}/{len(all_job_links)}] 完成")

            if idx < len(all_job_links):
                random_sleep("抓取间隔")

        logger.info(f"\n🎉 DeepSeek 抓取完成！共 {len(all_job_details)} 条")

    except Exception as e:
        logger.error(f"❌ 脚本运行失败: {e}", exc_info=True)
    finally:
        if page is not None:
            try: context.close()
            except Exception: pass
        if browser is not None:
            try: browser.close()
            except Exception: pass
        if p is not None:
            try: p.stop()
            except Exception: pass
        logger.info("✅ 浏览器已关闭")

    return all_job_details


def save_results(jobs: list[dict]):
    """保存结果到 data/openclaw_jobs.json（去重合并）"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    existing = []
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            logger.info(f"📂 读取已有数据: {len(existing)} 条")
        except Exception:
            existing = []

    existing_by_url = {j.get("url", ""): j for j in existing}
    for job in jobs:
        url = job.get("url", "")
        if url:
            existing_by_url[url] = job

    merged = list(existing_by_url.values())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 数据已保存到: {OUTPUT_FILE}")
    logger.info(f"  本次新增/更新 {len(jobs)} 条，累计 {len(merged)} 条")


def run_bridge():
    """自动触发 openclaw_bridge.py 同步到 Notion"""
    logger.info(f"\n{'='*60}")
    logger.info(f"🔄 自动触发桥接脚本: openclaw_bridge.py")
    logger.info(f"{'='*60}")

    try:
        env = dict(os.environ)
        env["FORCE_UPDATE"] = "1"
        result = subprocess.run(
            [sys.executable, str(BRIDGE_SCRIPT)],
            capture_output=False,
            text=True,
            env=env,
        )
        logger.info(f"✅ 桥接脚本执行完成 (返回码: {result.returncode})")
    except Exception as e:
        logger.error(f"❌ 桥接脚本执行失败: {e}")


def print_summary(jobs: list[dict]):
    """打印结果摘要"""
    print("\n" + "=" * 70)
    print("📊 DeepSeek 精准爬取结果")
    print("=" * 70)
    print(f"  共抓取到 {len(jobs)} 条岗位数据")
    print("-" * 70)

    for idx, job in enumerate(jobs, 1):
        print(f"\n  [{idx:2d}] {job['title']}")
        print(f"       公司: {job['company']}")
        print(f"       地点: {job['location']}")
        print(f"       链接: {job['url']}")
        if job.get("full_jd"):
            print(f"       描述: {job['full_jd'][:150]}...")
        if job.get("requirements"):
            print(f"       要求: {job['requirements'][:150]}...")

    print("\n" + "=" * 70)
    print(f"💾 完整数据已保存到: {OUTPUT_FILE}")
    print("=" * 70)


def main():
    print("\n" + "=" * 70)
    print("🔍 DeepSeek 精准爬虫 (Moka 系统)")
    print("=" * 70)
    print(f"  目标: {TARGET_URL}")
    print(f"  最大翻页: {MAX_PAGES} 页")
    print(f"  输出: {OUTPUT_FILE}")
    print(f"  自动桥接: 是 (FORCE_UPDATE=1)")
    print("=" * 70)

    jobs = crawl()

    if jobs:
        save_results(jobs)
        print_summary(jobs)
        run_bridge()
    else:
        logger.warning("⚠️ 未抓取到任何数据")


if __name__ == "__main__":
    main()
