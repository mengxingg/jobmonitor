#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
bytedance_visual_crawler.py — 字节跳动招聘视觉翻页抓取器 v2.0

核心策略：
  1. 使用 Playwright 启动有界面浏览器（headless=False）
  2. 访问字节跳动招聘筛选页
  3. 模拟真人视觉翻页：抓取当前页岗位 URL → 点击『下一页』→ 等待 2-3 秒
  4. 循环 5 页，收集所有岗位详情 URL（每页 10 条，共约 50 条）
  5. 逐个进入详情页，提取完整『职位描述』和『要求』
  6. 保存到 data/openclaw_jobs.json
  7. 自动触发 openclaw_bridge.py 同步到 Notion（含强制更新）

用法:
  python bytedance_visual_crawler.py
"""

import sys, io, json, time, logging, re, random, subprocess, os
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── 确保 UTF-8 输出 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 使用 Playwright ──
from playwright.sync_api import sync_playwright, Page, Browser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("visual_crawler")

# ── 配置 ──
TARGET_URL = (
    "https://jobs.bytedance.com/experienced/position"
    "?keywords=AI%20%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86"
    "&category=6704215864591255820"
    "&location=&project=&type=&job_hot_flag=&current=1&limit=10"
    "&functionCategory=&tag="
)
MAX_PAGES = 5          # 最多翻 5 页
MIN_WAIT = 2.0         # 随机等待下限（秒）
MAX_WAIT = 3.0         # 随机等待上限（秒）
PAGE_LOAD_TIMEOUT = 60000  # 页面加载超时（毫秒）
NAVIGATE_TIMEOUT = 30000   # 导航超时（毫秒）
OUTPUT_FILE = Path(__file__).parent / "data" / "openclaw_jobs.json"
BRIDGE_SCRIPT = Path(__file__).parent / "openclaw_bridge.py"


def random_sleep(label: str = ""):
    """随机等待 2-3 秒，模拟真人浏览节奏"""
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
    # 注入反检测脚本
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    """)
    page = context.new_page()
    page.set_default_timeout(PAGE_LOAD_TIMEOUT)
    logger.info("✅ 浏览器已启动 (viewport=1400x900)")
    return p, browser, context, page


def collect_job_links(page: Page) -> list[dict]:
    """
    从当前页面提取所有岗位卡片中的详情 URL 和标题。
    返回 [{"title": "...", "url": "..."}, ...]
    """
    jobs = []
    try:
        # 等待岗位列表加载
        page.wait_for_selector(
            "a[href*='/experienced/position/'], a[href*='/social-recruitment/']",
            timeout=10000,
        )
        time.sleep(1)  # 等待渲染完成
    except Exception:
        logger.warning("⚠️ 未找到岗位列表选择器，尝试备用方案...")

    # 方案1: 通过 <a> 标签提取岗位详情链接
    links = page.eval_on_selector_all(
        "a[href*='/experienced/position/']",
        """els => els.map(el => ({
            url: el.href,
            title: (el.querySelector('.job-title, .position-name, h3, h4')
                    || el).innerText.trim()
        }))"""
    )
    if links and len(links) > 0:
        logger.info(f"📋 方案1: 通过 <a> 标签提取到 {len(links)} 个岗位链接")
        for job in links:
            if job["url"] and job["title"]:
                jobs.append(job)
        return jobs

    # 方案2: 通过页面 JS 提取所有岗位卡片
    jobs = page.evaluate("""
        () => {
            const results = [];
            const selectors = [
                '.job-card', '.position-card', '.job-post-item',
                '.recruitment-item', '[class*="job"]', '[class*="position"]',
                'li[class*="item"]', 'tr[class*="item"]'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 0) break;
            }
            if (cards.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    if (a.href && a.href.includes('/experienced/position/')) {
                        results.push({
                            url: a.href,
                            title: a.innerText.trim().split('\\n')[0].trim()
                        });
                    }
                });
                return results;
            }
            cards.forEach(card => {
                const link = card.querySelector('a');
                const url = link ? link.href : '';
                const title = (card.querySelector('.job-title, .position-name, h3, h4, .title')
                    || card).innerText.trim().split('\\n')[0].trim();
                if (url && title) {
                    results.push({ url, title });
                }
            });
            return results;
        }
    """)

    if jobs and len(jobs) > 0:
        logger.info(f"📋 方案2: 通过 JS 提取到 {len(jobs)} 个岗位链接")
    else:
        logger.warning("⚠️ 未提取到任何岗位链接！")

    return jobs


def click_next_page(page: Page) -> bool:
    """
    点击『下一页』按钮。
    返回 True 表示成功点击，False 表示按钮不可用或不存在。
    """
    # 尝试多种『下一页』选择器
    next_selectors = [
        "button:has-text('下一页')",
        "a:has-text('下一页')",
        "[class*='next']:not([class*='disabled'])",
        "button[class*='next']:not([disabled])",
        ".pagination .next:not(.disabled)",
        ".pagination button:last-child:not([disabled])",
        "li.next:not(.disabled) a",
        "li.next:not(.disabled) button",
        "[aria-label='Next']",
        "[aria-label='下一页']",
        ".page-next:not(.disabled)",
    ]

    for selector in next_selectors:
        try:
            btn = page.query_selector(selector)
            if btn is None:
                continue

            # 检查是否 disabled
            is_disabled = btn.get_attribute("disabled") is not None
            class_attr = btn.get_attribute("class") or ""
            if is_disabled or "disabled" in class_attr:
                logger.info(f"🔚 『下一页』按钮已禁用 (selector={selector})")
                return False

            # 检查按钮是否可见
            is_visible = btn.is_visible()
            if not is_visible:
                continue

            # 滚动到按钮位置
            btn.scroll_into_view_if_needed()
            time.sleep(0.5)

            logger.info(f"👉 点击『下一页』按钮 (selector={selector})")
            btn.click()
            return True

        except Exception as e:
            logger.debug(f"  选择器 '{selector}' 失败: {e}")
            continue

    # 兜底：通过 JS 查找并点击
    try:
        result = page.evaluate("""
            () => {
                const allElements = document.querySelectorAll('button, a, li, span, div');
                for (const el of allElements) {
                    const text = el.innerText.trim();
                    if (text === '下一页' || text === 'Next' || text === 'next') {
                        if (el.disabled || el.classList.contains('disabled')) {
                            return 'disabled';
                        }
                        el.click();
                        return 'clicked';
                    }
                }
                const nextBtn = document.querySelector('[aria-label="下一页"], [aria-label="Next"]');
                if (nextBtn && !nextBtn.disabled && !nextBtn.classList.contains('disabled')) {
                    nextBtn.click();
                    return 'clicked';
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
    访问岗位详情页，提取完整的职位描述和要求。

    ★ v3.0 优化：使用 CSS 选择器精准定位"职位描述"和"职位要求"容器，
      分别提取为 job_description 和 job_requirements 两个独立字段。
    """
    logger.info(f"🔍 进入详情页: {title}")
    try:
        page.goto(url, timeout=NAVIGATE_TIMEOUT, wait_until="domcontentloaded")
        # 等待内容加载
        time.sleep(2)

        # ── ★ v3.0 核心优化：用 CSS 选择器精准定位各容器 ──
        # 字节跳动详情页的结构通常为：
        #   .job-detail-section 或 .job-description 包含完整 JD
        #   内部有 h3/h4 标题标识"职位描述"和"职位要求"
        #   标题后的下一个兄弟容器即为对应内容
        extracted = page.evaluate("""
            () => {
                // 1. 定位核心 JD 容器
                const containerSelectors = [
                    '.job-detail-section',
                    '.job-description',
                    '.position-description',
                    '.jd-content',
                    '[class*="job-detail"]',
                    '[class*="position-detail"]',
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
                    // 兜底：取 body 文本
                    const body = document.body;
                    const excludes = body.querySelectorAll(
                        'nav, footer, .nav, .footer, .header, .sidebar, '
                        + '[class*="nav"], [class*="footer"], [class*="sidebar"], '
                        + '[class*="header"], [class*="breadcrumb"]'
                    );
                    for (const el of excludes) { el.remove(); }
                    return { job_description: '', job_requirements: '', page_text: body.innerText };
                }

                const page_text = container.innerText;

                // 2. 在容器内查找"职位描述"和"职位要求"的标题元素
                //    字节跳动详情页通常使用 h3/h4/strong 作为标题
                const headingSelectors = ['h3', 'h4', 'h5', 'strong', 'b', '.section-title', '[class*="title"]'];
                let descHeading = null;
                let reqHeading = null;

                // 收集容器内所有可能的标题元素
                const allHeadings = container.querySelectorAll(headingSelectors.join(','));
                for (const h of allHeadings) {
                    const text = h.innerText.trim();
                    if (/^(职位描述|岗位职责|Job Description)/i.test(text)) {
                        descHeading = h;
                    } else if (/^(职位要求|任职要求|岗位要求|任职资格|我们希望你|关于你|Requirements|Qualifications)/i.test(text)) {
                        reqHeading = h;
                    }
                }

                // 3. 提取标题后的内容
                //    策略：取标题元素的下一个兄弟元素，或标题后的所有文本直到下一个标题
                function extractContentAfter(heading) {
                    if (!heading) return '';
                    let content = '';
                    // 尝试取下一个兄弟元素
                    let next = heading.nextElementSibling;
                    if (next) {
                        content = next.innerText.trim();
                    }
                    // 如果兄弟元素内容太少，尝试取标题后的所有文本直到下一个标题
                    if (content.length < 20) {
                        let node = heading.nextSibling;
                        const parts = [];
                        while (node) {
                            if (node.nodeType === Node.TEXT_NODE) {
                                parts.push(node.textContent);
                            } else if (node.nodeType === Node.ELEMENT_NODE) {
                                // 如果遇到另一个标题则停止
                                const tag = node.tagName.toLowerCase();
                                if (['h3', 'h4', 'h5'].includes(tag)) break;
                                parts.push(node.innerText);
                            }
                            node = node.nextSibling;
                        }
                        content = parts.join('\\n').trim();
                    }
                    return content;
                }

                let job_description = extractContentAfter(descHeading);
                let job_requirements = extractContentAfter(reqHeading);

                // 4. 如果 CSS 选择器方式提取失败，回退到正则分割
                if (!job_description && !job_requirements) {
                    // 按常见标题分割
                    const jdMatch = page_text.match(/职位描述[：:]\\s*([\\s\\S]*?)(?=职位要求|任职要求|岗位要求|任职资格|我们希望你|关于你|【|$)/);
                    const reqMatch = page_text.match(/(?:职位要求|任职要求|岗位要求|任职资格|我们希望你|关于你)[：:]\\s*([\\s\\S]*?)(?=\\n\\n|\\n#|\\n##|$)/);
                    if (jdMatch) job_description = jdMatch[1].trim();
                    if (reqMatch) job_requirements = reqMatch[1].trim();
                }

                // 5. 如果仍然失败，整段文本作为描述
                if (!job_description && !job_requirements) {
                    job_description = page_text.slice(0, 2000);
                }

                return { job_description, job_requirements, page_text };
            }
        """)

        job_description = extracted.get("job_description", "")
        job_requirements = extracted.get("job_requirements", "")
        page_text = extracted.get("page_text", "")

        # 清理文本（保留换行结构，仅合并多余空白）
        job_description = re.sub(r"[ \t]+", " ", job_description).strip()
        job_requirements = re.sub(r"[ \t]+", " ", job_requirements).strip()

        logger.info(f"  ✅ 提取完成: 描述 {len(job_description)} 字符, 要求 {len(job_requirements)} 字符")

        return {
            "title": title,
            "company": "字节跳动 (ByteDance)",
            "salary": "未披露",
            "location": _extract_location(page_text, url),
            "url": url,
            "job_description": job_description,
            "job_requirements": job_requirements,
            # 保留旧字段名以兼容下游
            "full_jd": job_description,
            "requirements": job_requirements,
        }

    except Exception as e:
        logger.error(f"  ❌ 详情页提取失败: {e}")
        return None


def _extract_location(page_text: str, url: str) -> str:
    """从页面文本或 URL 中提取地点"""
    city_pattern = r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆|苏州|长沙|天津|郑州|东莞|青岛|厦门|合肥|佛山|宁波|昆明|沈阳|大连|济南|哈尔滨|福州|无锡|贵阳|南昌|珠海|中山|惠州|温州|嘉兴|绍兴|泉州|南通|常州|徐州|太原|石家庄|长春|兰州|乌鲁木齐|海口|南宁|呼和浩特|银川|西宁|拉萨)"
    cities = re.findall(city_pattern, page_text)
    if cities:
        from collections import Counter
        return Counter(cities).most_common(1)[0][0]
    return "北京"


def crawl() -> list[dict]:
    """主抓取流程"""
    p = browser = context = page = None
    all_job_links = []
    all_job_details = []

    try:
        # ── 启动浏览器 ──
        p, browser, context, page = init_browser()

        # ── 访问目标页面 ──
        logger.info(f"🌐 访问字节跳动招聘页面...")
        logger.info(f"   URL: {TARGET_URL}")
        page.goto(TARGET_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        logger.info("✅ 页面加载完成")

        # 等待页面稳定
        random_sleep("等待页面渲染稳定")

        # ── 循环翻页抓取 ──
        for page_num in range(1, MAX_PAGES + 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📄 第 {page_num}/{MAX_PAGES} 页")
            logger.info(f"{'='*60}")

            # 抓取当前页的岗位链接
            page_jobs = collect_job_links(page)
            logger.info(f"📌 当前页获取到 {len(page_jobs)} 个岗位")

            # 去重
            existing_urls = {j["url"] for j in all_job_links}
            new_jobs = [j for j in page_jobs if j["url"] not in existing_urls]
            if new_jobs:
                all_job_links.extend(new_jobs)
                logger.info(f"✨ 新增 {len(new_jobs)} 个岗位，累计 {len(all_job_links)} 个")
            else:
                logger.info(f"💡 当前页无新岗位（可能翻页后内容未刷新），累计 {len(all_job_links)} 个")

            # 如果不是最后一页，点击『下一页』
            if page_num < MAX_PAGES:
                random_sleep("翻页前模拟浏览")
                clicked = click_next_page(page)
                if not clicked:
                    logger.info("🏁 已到达最后一页，停止翻页")
                    break
                # 等待新页面加载 —— 关键：等待 networkidle 确保新数据到达
                logger.info("⏳ 等待新页面数据加载...")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    logger.debug("  networkidle 超时，继续...")
                # 额外等待确保 DOM 更新
                time.sleep(2)
            else:
                logger.info(f"✅ 已达到最大翻页数 {MAX_PAGES}")

        logger.info(f"\n📊 共收集到 {len(all_job_links)} 个岗位 URL")

        # ── 逐个访问详情页 ──
        logger.info(f"\n{'='*60}")
        logger.info(f"🔎 开始深度抓取岗位详情 ({len(all_job_links)} 个)")
        logger.info(f"{'='*60}")

        for idx, job in enumerate(all_job_links, 1):
            logger.info(f"\n[{idx}/{len(all_job_links)}] 正在抓取: {job['title']}")
            detail = extract_job_detail(page, job["url"], job["title"])
            if detail:
                all_job_details.append(detail)
                logger.info(f"  ✅ [{idx}/{len(all_job_links)}] 完成")

            # 随机等待，避免被限流
            if idx < len(all_job_links):
                random_sleep("抓取间隔")

        logger.info(f"\n🎉 深度抓取完成！共获取 {len(all_job_details)} 条完整数据")

    except Exception as e:
        logger.error(f"❌ 脚本运行失败: {e}", exc_info=True)
    finally:
        # ── 关闭浏览器 ──
        if page is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass
        logger.info("✅ 浏览器已关闭")

    return all_job_details


def save_results(jobs: list[dict]):
    """保存结果到 data/openclaw_jobs.json"""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 读取已有数据（去重合并）
    existing = []
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            logger.info(f"📂 读取已有数据: {len(existing)} 条")
        except Exception:
            existing = []

    # 去重合并（按 URL 去重，新数据覆盖旧数据）
    existing_by_url = {j.get("url", ""): j for j in existing}
    for job in jobs:
        url = job.get("url", "")
        if url:
            existing_by_url[url] = job  # 新数据覆盖旧数据

    merged = list(existing_by_url.values())

    # 写入
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
        # 使用 FORCE_UPDATE=1 环境变量，让桥接脚本强制更新已存在的 Notion 页面
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
    print("📊 字节跳动视觉翻页抓取结果")
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
    print("🔍 字节跳动招聘视觉翻页抓取器 v2.0")
    print("=" * 70)
    print(f"  目标: {TARGET_URL}")
    print(f"  最大翻页: {MAX_PAGES} 页")
    print(f"  输出: {OUTPUT_FILE}")
    print(f"  浏览器模式: 有界面 (headless=False)")
    print(f"  自动桥接: 是 (FORCE_UPDATE=1)")
    print("=" * 70)

    jobs = crawl()

    if jobs:
        save_results(jobs)
        print_summary(jobs)
        # 自动触发桥接
        run_bridge()
    else:
        logger.warning("⚠️ 未抓取到任何数据")


if __name__ == "__main__":
    main()
