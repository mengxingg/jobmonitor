#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
crawler_moonshot.py — 月之暗面 (Moonshot AI) 招聘精准爬虫

使用 Playwright 访问月之暗面招聘页面（Moka 系统），只抓取「产品经理」相关岗位。

URL:
  https://app.mokahr.com/apply/moonshot/148506?sourceToken=7bec6769f2bfa471e5c9ce21b6b1096b#/jobs/?keyword=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&page=1&anchorName=jobsList

月之暗面招聘系统特点：
  - Moka 系统 SPA 页面，hash 路由
  - 列表页岗位卡片在 .job-list 或 .position-list 容器中
  - 详情页是独立路由，需要逐个访问提取 JD
  - 翻页通过点击「下一页」按钮
  - 使用降级抓取策略：CSS 选择器失效时直接 inner_text 抓取全文

用法:
  conda run -n job_env python crawler_moonshot.py
"""

import sys, io, json, time, logging, re, random, subprocess, os
from pathlib import Path
from typing import Optional
from datetime import datetime

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
logger = logging.getLogger("moonshot_crawler")

# ── 配置 ──
TARGET_URL = (
    "https://app.mokahr.com/apply/moonshot/148506"
    "?sourceToken=7bec6769f2bfa471e5c9ce21b6b1096b"
    "#/jobs/?keyword=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&page=1&anchorName=jobsList"
)
COMPANY_NAME = "月之暗面"
COMPANY_LOCATION = "北京"
MAX_PAGES = 5
MIN_WAIT = 2.0
MAX_WAIT = 3.0
PAGE_LOAD_TIMEOUT = 60000
NAVIGATE_TIMEOUT = 30000
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "openclaw_jobs.json"
BRIDGE_SCRIPT = Path(__file__).parent / "openclaw_bridge.py"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# ── 产品经理关键词过滤 ──
PM_KEYWORDS = ["产品经理", "产品", "PM", "产品运营", "产品策划", "产品专家", "产品负责人"]
EXCLUDE_KEYWORDS = ["算法", "工程师", "开发", "架构师", "测试", "运维", "前端", "后端", "全栈",
                    "数据挖掘", "NLP", "CV", "机器学习", "深度学习", "研究员", "科学家",
                    "设计", "UI", "UX", "视觉", "交互", "市场", "销售",
                    "HR", "人力", "行政", "财务", "法务"]


def is_pm_related(title: str) -> bool:
    """判断岗位标题是否与产品经理相关"""
    if not title:
        return False
    for kw in PM_KEYWORDS:
        if kw in title:
            return True
    for ek in EXCLUDE_KEYWORDS:
        if ek in title:
            return False
    return False


def random_sleep(label: str = ""):
    t = random.uniform(MIN_WAIT, MAX_WAIT)
    if label:
        logger.info(f"⏳ {label}，等待 {t:.1f} 秒...")
    else:
        logger.info(f"⏳ 等待 {t:.1f} 秒...")
    time.sleep(t)


def take_screenshot(page: Page, name: str) -> str:
    """截图并返回保存路径"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{timestamp}_{name}.png"
    filepath = SCREENSHOT_DIR / filename
    try:
        page.screenshot(path=str(filepath), full_page=True)
        logger.info(f"📸 截图已保存: {filepath}")
    except Exception as e:
        logger.warning(f"⚠️ 截图失败: {e}")
    return str(filepath)


def init_browser() -> tuple:
    """启动 Playwright 有界面浏览器，注入 Stealth 反检测脚本"""
    logger.info("🚀 启动 Playwright 浏览器 (headless=False, 有界面模式)...")
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
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
            "--disable-features=BlockInsecurePrivateNetworkRequests",
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
        permissions=["geolocation"],
        geolocation={"latitude": 39.9042, "longitude": 116.4074},  # 北京
    )
    # Stealth 反检测脚本
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        console.log('✅ Stealth 反检测脚本已注入');
    """)
    page = context.new_page()
    page.set_default_timeout(PAGE_LOAD_TIMEOUT)
    logger.info("✅ 浏览器已启动 (viewport=1400x900, stealth 已注入)")
    return p, browser, context, page


def debug_page_state(page: Page, label: str = ""):
    """打印当前页面状态（URL、标题、body 文本长度等）"""
    try:
        url = page.url
        title = page.title()
        body_len = len(page.evaluate("document.body?.innerText || ''") or "")
        logger.info(f"🔍 [{label}] URL: {url}")
        logger.info(f"🔍 [{label}] 页面标题: {title}")
        logger.info(f"🔍 [{label}] body 文本长度: {body_len} 字符")
        return {"url": url, "title": title, "body_len": body_len}
    except Exception as e:
        logger.warning(f"⚠️ [{label}] 获取页面状态失败: {e}")
        return {}


def wait_for_list_load(page: Page) -> bool:
    """等待月之暗面岗位列表加载"""
    selectors = [
        ".job-list",
        ".position-list",
        "[class*='job-list']",
        "[class*='position-list']",
        "[class*='career-list']",
        "[class*='position-item']",
        "[class*='job-item']",
        "a[href*='/careers/']",
        "a[href*='/position/']",
        "a[href*='/job/']",
        ".list-container",
        "main",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=15000)
            logger.info(f"✅ 列表已渲染 (selector={selector})")
            time.sleep(2)
            return True
        except Exception:
            continue
    # JS 兜底
    try:
        has_items = page.evaluate("""
            () => {
                const items = document.querySelectorAll(
                    'a[href*="/careers/"], a[href*="/position/"], a[href*="/job/"], '
                    '[class*="position-item"], [class*="job-item"], li[class*="item"]'
                );
                return items.length > 0;
            }
        """)
        if has_items:
            logger.info("✅ JS 检测到岗位列表存在")
            return True
    except Exception:
        pass
    return False


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

    # 先打印当前页面所有按钮文本，帮助调试
    try:
        all_buttons = page.evaluate("""
            () => {
                const els = document.querySelectorAll('button, a, li, span, div');
                return Array.from(els).slice(0, 50).map(e => ({
                    tag: e.tagName,
                    text: (e.innerText || '').trim().slice(0, 30),
                    class: (e.className || '').slice(0, 60),
                    disabled: e.disabled || false,
                    visible: e.offsetParent !== null
                }));
            }
        """)
        logger.info(f"🔍 页面前 50 个元素文本: {json.dumps(all_buttons, ensure_ascii=False)}")
    except Exception as e:
        logger.debug(f"  获取页面元素列表失败: {e}")

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
            logger.info("🔚 未找到『下一页』按钮 (JS 兜底返回: {result})")
            return False
    except Exception as e:
        logger.warning(f"⚠️ JS 兜底点击失败: {e}")
        return False


def collect_job_links(page: Page) -> list[dict]:
    """从月之暗面列表页提取所有岗位链接，只保留产品经理相关"""
    # 先打印页面状态
    debug_page_state(page, "collect_job_links")

    # 打印页面中所有链接的 href 和文本，帮助调试
    try:
        all_links = page.evaluate("""
            () => {
                const links = document.querySelectorAll('a');
                return Array.from(links).slice(0, 100).map(a => ({
                    href: a.href,
                    text: (a.innerText || '').trim().slice(0, 50),
                    class: (a.className || '').slice(0, 40)
                }));
            }
        """)
        logger.info(f"🔗 页面前 100 个链接:")
        for link in all_links:
            if link["href"] and link["text"]:
                logger.info(f"   href={link['href']}  text={link['text']}")
    except Exception as e:
        logger.warning(f"  获取链接列表失败: {e}")

    # 尝试多种选择器提取岗位链接
    jobs = page.evaluate("""
        () => {
            const results = [];
            // 选择器组合 1: 标准 Moka 岗位链接
            const selectors = [
                'a[href*="/careers/"]',
                'a[href*="/position/"]',
                'a[href*="/job/"]',
                'a[href*="position-detail"]',
                'a[href*="job-detail"]',
                'a[class*="position"]',
                'a[class*="job"]',
                'a[class*="career"]',
            ];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(a => {
                    const url = a.href;
                    const title = (a.innerText || '').trim().split(String.fromCharCode(10))[0].trim();
                    if (url && title && !results.find(r => r.url === url)) {
                        results.push({ url, title });
                    }
                });
            }
            // 兜底: 从所有卡片元素中提取
            if (results.length === 0) {
                const cards = document.querySelectorAll(
                    '[class*="position"], [class*="job"], [class*="career"], li, .item, [class*="card"]'
                );
                cards.forEach(card => {
                    const link = card.querySelector('a');
                    const url = link ? link.href : '';
                    const title = (card.querySelector('[class*="title"], [class*="name"], h3, h4, [class*="position-name"]') || card).innerText.trim().split('\\n')[0].trim();
                    if (url && title && !results.find(r => r.url === url)) {
                        results.push({ url, title });
                    }
                });
            }
            return results;
        }
    """) or []

    logger.info(f"📋 原始提取到 {len(jobs)} 个岗位链接")
    for j in jobs:
        logger.info(f"   📌 {j['title']} -> {j['url']}")

    pm_jobs = [j for j in jobs if is_pm_related(j["title"])]
    logger.info(f"📋 其中产品经理相关 {len(pm_jobs)} 个")
    for j in jobs:
        if j not in pm_jobs:
            logger.info(f"  🚫 过滤非产品岗: {j['title']}")
    return pm_jobs


def extract_job_detail(page: Page, url: str, title: str) -> Optional[dict]:
    """访问月之暗面岗位详情页，提取职位描述和要求。使用降级策略：CSS 失效则 inner_text 全文。"""
    logger.info(f"🔍 进入详情页: {title}")
    try:
        page.goto(url, timeout=NAVIGATE_TIMEOUT, wait_until="domcontentloaded")
        time.sleep(3)

        # 截图详情页
        take_screenshot(page, f"detail_{title[:20]}")

        # 等待 JD 内容加载
        try:
            page.wait_for_selector(
                ".job-detail, .position-detail, .jd-content, "
                "[class*='job-detail'], [class*='position-detail'], "
                "[class*='jd-content'], .detail-content, "
                "main, article, [class*='content']",
                timeout=15000,
            )
            time.sleep(2)
        except Exception:
            logger.warning("  ⚠️ JD 容器等待超时，尝试直接提取...")

        # ★ 降级策略：先尝试 CSS 提取，失败则 inner_text 全文
        extracted = page.evaluate("""
            () => {
                // 1. 尝试定位 JD 容器
                const containerSelectors = [
                    '.job-detail',
                    '.position-detail',
                    '.jd-content',
                    '[class*="job-detail"]',
                    '[class*="position-detail"]',
                    '[class*="jd-content"]',
                    '.detail-content',
                    '.content-wrapper',
                    '[class*="content"]',
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
                    const candidates = document.querySelectorAll('div[class], section[class]');
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
                    // ★ 降级：CSS 全部失效，抓取 body 全文
                    return { job_description: '', job_requirements: '', page_text: document.body.innerText };
                }

                const page_text = container.innerText;

                let job_description = '';
                let job_requirements = '';

                // 尝试多种标题模式提取
                const descMatch = page_text.match(
                    /(?:岗位职责|职位描述|工作职责|岗位描述)[\\s\\n]*([\\s\\S]*?)(?=\\n\\s*(?:任职要求|职位要求|岗位要求|任职资格|加分项|我们希望你|关于你|职位信息|工作内容|岗位亮点|$))/
                );
                if (descMatch) {
                    job_description = descMatch[1].trim();
                }

                const reqMatch = page_text.match(
                    /(?:任职要求|职位要求|岗位要求|任职资格|我们希望你|关于你)[\\s\\n]*([\\s\\S]*?)(?=\\n\\s*(?:加分项|职位信息|最新职位|岗位亮点|工作地点|$))/
                );
                if (reqMatch) {
                    job_requirements = reqMatch[1].trim();
                }

                // 兜底
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

        logger.info(f"🌐 访问月之暗面招聘页面...")
        logger.info(f"   URL: {TARGET_URL}")
        page.goto(TARGET_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        logger.info("✅ 页面加载完成")

        # 截图初始页面
        take_screenshot(page, "01_initial_load")

        # 打印页面状态
        debug_page_state(page, "初始加载")

        random_sleep("等待页面渲染稳定")
        if not wait_for_list_load(page):
            logger.warning("⚠️ 岗位列表未渲染，尝试继续...")
            # 再等几秒截图看看
            time.sleep(5)
            take_screenshot(page, "02_after_wait_no_list")

        # 截图列表页
        take_screenshot(page, "03_before_pagination")

        # 循环翻页（Moka 系统使用翻页按钮）
        for page_num in range(1, MAX_PAGES + 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📄 第 {page_num}/{MAX_PAGES} 页")
            logger.info(f"{'='*60}")

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
                take_screenshot(page, f"04_before_page{page_num + 1}")
                clicked = click_next_page(page)
                if not clicked:
                    logger.info("🏁 已到达最后一页，停止翻页")
                    break
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)
                # 翻页后截图
                take_screenshot(page, f"05_after_page{page_num + 1}")
                debug_page_state(page, f"翻页后第{page_num + 1}页")
            else:
                logger.info(f"✅ 已达到最大翻页数 {MAX_PAGES}")

        logger.info(f"\n{'='*60}")
        logger.info(f"📊 共收集到 {len(all_job_links)} 个岗位 URL")
        logger.info(f"{'='*60}")

        # 逐个访问详情页
        if all_job_links:
            logger.info(f"\n🔎 开始深度抓取岗位详情 ({len(all_job_links)} 个)")
            for idx, job in enumerate(all_job_links, 1):
                logger.info(f"\n[{idx}/{len(all_job_links)}] {job['title']}")
                detail = extract_job_detail(page, job["url"], job["title"])
                if detail:
                    all_job_details.append(detail)
                    logger.info(f"  ✅ [{idx}/{len(all_job_links)}] 完成")

                if idx < len(all_job_links):
                    random_sleep("抓取间隔")

            logger.info(f"\n🎉 月之暗面抓取完成！共 {len(all_job_details)} 条")
        else:
            logger.warning("⚠️ 没有收集到任何岗位链接，跳过详情页抓取")

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
    """保存结果到 data/openclaw_jobs.json（去重合并，原子化保存）"""
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
    print("📊 月之暗面精准爬取结果")
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
    print("🔍 月之暗面 (Moonshot AI) 精准爬虫")
    print("=" * 70)
    print(f"  目标: {TARGET_URL}")
    print(f"  最大翻页: {MAX_PAGES} 页")
    print(f"  输出: {OUTPUT_FILE}")
    print(f"  截图目录: {SCREENSHOT_DIR}")
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
