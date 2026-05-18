#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
crawler_xiaohongshu.py — 小红书招聘精准爬虫

使用 Playwright + Stealth 插件访问小红书招聘精准搜索 URL，
只抓取「AI 产品经理」相关岗位，提取职位描述和任职要求。

URL:
  https://job.xiaohongshu.com/social/position?positionName=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86

小红书招聘系统特点：
  - SPA 页面，岗位列表通过 JS 异步加载
  - 需要滚动加载更多（无限滚动）
  - 详情页是独立路由，需要逐个访问提取 JD
  - 有反爬检测，需要使用 Stealth 插件

用法:
  conda run -n job_env python crawler_xiaohongshu.py
"""

import sys, io, json, time, logging, re, random, subprocess, os
from pathlib import Path
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
logger = logging.getLogger("xhs_crawler")

# ── 配置 ──
TARGET_URL = "https://job.xiaohongshu.com/social/position?positionName=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86"
COMPANY_NAME = "小红书"
COMPANY_LOCATION = "上海"
MAX_SCROLLS = 10          # 最多滚动 10 次加载更多
SCROLL_WAIT = 2.0         # 每次滚动后等待秒数
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
    """
    启动 Playwright 有界面浏览器，注入 Stealth 反检测脚本。
    小红书有较强的反爬机制，需要模拟真实浏览器环境。
    """
    logger.info("🚀 启动 Playwright 浏览器 (headless=False, stealth)...")
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
        geolocation={"latitude": 31.2304, "longitude": 121.4737},
    )
    # ★ Stealth 反检测脚本
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
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter(parameter);
        };
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });
        console.log('Stealth injected');
    """)
    page = context.new_page()
    page.set_default_timeout(PAGE_LOAD_TIMEOUT)
    logger.info("✅ 浏览器已启动 (viewport=1400x900, stealth 已注入)")
    return p, browser, context, page


def wait_for_list_load(page: Page) -> bool:
    """等待小红书岗位列表加载。"""
    selectors = [
        ".position-list", ".job-list",
        "[class*='position-list']", "[class*='job-list']",
        "a[href*='/social/position/']", "a[href*='/position/detail']",
        ".list-container", "[class*='list-container']",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=15000)
            logger.info(f"✅ 列表已渲染 (selector={selector})")
            time.sleep(2)
            return True
        except Exception:
            continue
    try:
        has_items = page.evaluate(
            "() => {"
            "const items = document.querySelectorAll("
            "'a[href*=\"/social/position/\"], a[href*=\"/position/detail\"], '"
            "'[class*=\"position-item\"], [class*=\"job-item\"], li[class*=\"item\"]');"
            "return items.length > 0;"
            "}"
        )
        if has_items:
            logger.info("✅ JS 检测到岗位列表存在")
            return True
    except Exception:
        pass
    return False


def scroll_to_load_more(page: Page) -> int:
    """模拟真人滚动到页面底部，触发无限加载。"""
    before_count = len(page.evaluate(
        "() => {"
        "const links = document.querySelectorAll('a[href*=\"/social/position/\"], a[href*=\"/position/detail\"]');"
        "return Array.from(links).map(a => a.href);"
        "}"
    ) or [])

    document_height = page.evaluate("document.body.scrollHeight") or 8000

    logger.info("📜 模拟滚动加载更多...")
    for step in range(1, 6):
        scroll_y = int(step * (document_height / 5))
        page.evaluate(f"window.scrollTo(0, {scroll_y})")
        time.sleep(0.5)

    time.sleep(SCROLL_WAIT)

    after_links = page.evaluate(
        "() => {"
        "const links = document.querySelectorAll('a[href*=\"/social/position/\"], a[href*=\"/position/detail\"]');"
        "return Array.from(links).map(a => a.href);"
        "}"
    ) or []
    after_count = len(after_links)

    new_count = after_count - before_count
    if new_count > 0:
        logger.info(f"📌 滚动加载了 {new_count} 个新岗位 (总计 {after_count})")
    else:
        logger.info(f"📌 滚动后无新岗位 (总计 {after_count})")
    return new_count


# ── 产品经理关键词过滤列表 ──
PM_KEYWORDS = ["产品经理", "产品", "PM", "产品运营", "产品策划", "产品专家", "产品负责人"]


def is_pm_related(title: str) -> bool:
    """判断岗位标题是否与产品经理相关"""
    if not title:
        return False
    for kw in PM_KEYWORDS:
        if kw in title:
            return True
    exclude_keywords = ["算法", "工程师", "开发", "架构师", "测试", "运维", "前端", "后端", "全栈",
                        "数据挖掘", "NLP", "CV", "机器学习", "深度学习", "研究员", "科学家",
                        "设计", "UI", "UX", "视觉", "交互", "市场", "销售",
                        "HR", "人力", "行政", "财务", "法务"]
    for ek in exclude_keywords:
        if ek in title:
            return False
    return False


def collect_job_links(page: Page) -> list[dict]:
    """
    从小红书列表页提取所有岗位链接。
    使用单行 JS 避免多行模板字符串语法错误。
    """
    js_code = (
        "() => {"
        "const results = [];"
        "const links = document.querySelectorAll('a[href*=\"/social/position/\"]');"
        "links.forEach(a => {"
        "const url = a.href;"
        "if (url === 'https://job.xiaohongshu.com/social/position') return;"
        "if (url === 'https://job.xiaohongshu.com/') return;"
        "const fullText = a.innerText.trim();"
        "const lines = fullText.split(String.fromCharCode(10));"
        "const title = lines[0].trim();"
        "if (url && title && !results.find(r => r.url === url)) {"
        "results.push({ url, title });"
        "}"
        "});"
        "return results;"
        "}"
    )
    jobs = page.evaluate(js_code) or []

    pm_jobs = [j for j in jobs if is_pm_related(j["title"])]
    logger.info(f"📋 提取到 {len(jobs)} 个，其中产品经理相关 {len(pm_jobs)} 个")
    for j in jobs:
        if j not in pm_jobs:
            logger.info(f"  🚫 过滤非产品岗: {j['title']}")
    return pm_jobs


def extract_job_detail(page: Page, url: str, title: str) -> Optional[dict]:
    """访问小红书岗位详情页，提取职位描述和要求。"""
    logger.info(f"🔍 进入详情页: {title}")
    try:
        page.goto(url, timeout=NAVIGATE_TIMEOUT, wait_until="domcontentloaded")
        time.sleep(3)

        try:
            page.wait_for_selector(
                ".job-detail, .position-detail, .jd-content, "
                "[class*='job-detail'], [class*='position-detail'], "
                "[class*='jd-content'], .detail-content, main, article",
                timeout=15000,
            )
            time.sleep(2)
        except Exception:
            logger.warning("  ⚠️ JD 容器等待超时，尝试直接提取...")

        extracted = page.evaluate(
            "() => {"
            "const containerSelectors = ["
            "'.job-detail', '.position-detail', '.jd-content',"
            "'[class*=\"job-detail\"]', '[class*=\"position-detail\"]', '[class*=\"jd-content\"]',"
            "'.detail-content', '.content-wrapper', 'main', 'article'"
            "];"
            "let container = null;"
            "for (const sel of containerSelectors) {"
            "const el = document.querySelector(sel);"
            "if (el && el.innerText.trim().length > 100) { container = el; break; }"
            "}"
            "if (!container) {"
            "const candidates = document.querySelectorAll('div[class], section[class]');"
            "let maxLen = 0;"
            "for (const el of candidates) {"
            "const len = el.innerText.trim().length;"
            "if (len > maxLen && len < 50000) { maxLen = len; container = el; }"
            "}"
            "}"
            "if (!container) {"
            "return { job_description: '', job_requirements: '', page_text: document.body.innerText };"
            "}"
            "const page_text = container.innerText;"
            "let job_description = '';"
            "let job_requirements = '';"
            "const descMatch = page_text.match(/(?:岗位职责|职位描述|工作职责|岗位描述)[\\s\\S]*?(?=任职要求|职位要求|岗位要求|任职资格|加分项|我们希望你|关于你|职位信息|工作内容|岗位亮点|$)/);"
            "if (descMatch) { job_description = descMatch[0].trim(); }"
            "const reqMatch = page_text.match(/(?:任职要求|职位要求|岗位要求|任职资格|我们希望你|关于你)[\\s\\S]*?(?=加分项|职位信息|最新职位|岗位亮点|工作地点|$)/);"
            "if (reqMatch) { job_requirements = reqMatch[0].trim(); }"
            "if (!job_description && !job_requirements) { job_description = page_text.slice(0, 3000); }"
            "return { job_description, job_requirements, page_text };"
            "}"
        )

        job_description = extracted.get("job_description", "")
        job_requirements = extracted.get("job_requirements", "")

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

        logger.info(f"🌐 访问小红书招聘页面 (精准搜索: AI 产品经理)...")
        logger.info(f"   URL: {TARGET_URL}")
        page.goto(TARGET_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        logger.info("✅ 页面加载完成")

        random_sleep("等待页面渲染稳定")
        if not wait_for_list_load(page):
            logger.warning("⚠️ 岗位列表未渲染，尝试继续...")

        logger.info(f"\n📜 开始滚动加载 (最多 {MAX_SCROLLS} 次)...")
        for scroll_num in range(1, MAX_SCROLLS + 1):
            logger.info(f"\n📄 滚动加载第 {scroll_num}/{MAX_SCROLLS} 次")
            new_count = scroll_to_load_more(page)
            if new_count == 0 and scroll_num > 2:
                logger.info("🏁 连续无新内容，停止滚动")
                break

        logger.info(f"\n📊 开始收集岗位链接...")
        all_job_links = collect_job_links(page)
        logger.info(f"📊 共收集到 {len(all_job_links)} 个岗位 URL")

        logger.info(f"\n🔎 开始深度抓取岗位详情 ({len(all_job_links)} 个)")
        for idx, job in enumerate(all_job_links, 1):
            logger.info(f"\n[{idx}/{len(all_job_links)}] {job['title']}")
            detail = extract_job_detail(page, job["url"], job["title"])
            if detail:
                all_job_details.append(detail)
                logger.info(f"  ✅ [{idx}/{len(all_job_links)}] 完成")
            if idx < len(all_job_links):
                random_sleep("抓取间隔")

        logger.info(f"\n🎉 小红书抓取完成！共 {len(all_job_details)} 条")

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
            capture_output=False, text=True, env=env,
        )
        logger.info(f"✅ 桥接脚本执行完成 (返回码: {result.returncode})")
    except Exception as e:
        logger.error(f"❌ 桥接脚本执行失败: {e}")


def print_summary(jobs: list[dict]):
    """打印结果摘要"""
    print("\n" + "=" * 70)
    print("📊 小红书精准爬取结果")
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
    print("🔍 小红书精准爬虫 (AI 产品经理)")
    print("=" * 70)
    print(f"  目标: {TARGET_URL}")
    print(f"  最大滚动: {MAX_SCROLLS} 次")
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
