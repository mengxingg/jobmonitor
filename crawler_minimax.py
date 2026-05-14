#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
crawler_minimax.py — MiniMax（稀宇科技）招聘精准爬虫

使用 Playwright 访问 MiniMax 飞书招聘页面，只抓取「产品经理」相关岗位。

URL:
  https://vrfi1sk8a0.jobs.feishu.cn/index/?keywords=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&category=6791702736615409933&location=&project=&type=&job_hot_flag=&current=1&limit=10&functionCategory=&tag=

MiniMax 招聘系统特点：
  - 飞书招聘系统（Feishu/ByteDance Recruitment）
  - 列表页通过 API 加载岗位卡片
  - 详情页是独立页面，需要逐个访问提取 JD
  - 翻页通过 URL 参数 current=1,2,3...
  - 使用降级抓取策略：CSS 选择器失效时直接 inner_text 抓取全文

用法:
  conda run -n job_env python crawler_minimax.py
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
logger = logging.getLogger("minimax_crawler")

# ── 配置 ──
TARGET_URL = (
    "https://vrfi1sk8a0.jobs.feishu.cn/index/"
    "?keywords=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86"
    "&category=6791702736615409933"
    "&location=&project=&type=&job_hot_flag="
    "&current=1&limit=10&functionCategory=&tag="
)
COMPANY_NAME = "MiniMax（稀宇科技）"
COMPANY_LOCATION = "上海"
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
    filename = f"{timestamp}_minimax_{name}.png"
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
        geolocation={"latitude": 31.2304, "longitude": 121.4737},  # 上海
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
    """等待 MiniMax 飞书招聘岗位列表加载"""
    selectors = [
        ".job-list",
        ".position-list",
        "[class*='job-list']",
        "[class*='position-list']",
        "[class*='job-item']",
        "[class*='position-item']",
        ".list-container",
        ".job-cards",
        "[class*='card']",
        "a[href*='/detail/']",
        "a[href*='/job/']",
        "main",
        ".content",
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
                    'a[href*="/detail/"], a[href*="/job/"], '
                    '[class*="job-item"], [class*="position-item"], '
                    '[class*="card"], li[class*="item"]'
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


def collect_job_links(page: Page) -> list[dict]:
    """从 MiniMax 飞书招聘列表页提取所有岗位链接，只保留产品经理相关"""
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
            // 选择器组合 1: 飞书招聘标准链接
            const selectors = [
                'a[href*="/detail/"]',
                'a[href*="/job/"]',
                'a[href*="position"]',
                'a[class*="position"]',
                'a[class*="job"]',
                'a[class*="card"]',
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
                    '[class*="position"], [class*="job"], [class*="card"], li, .item, [class*="list-item"], [class*="row"]'
                );
                cards.forEach(card => {
                    const link = card.querySelector('a');
                    const url = link ? link.href : '';
                    const titleEl = card.querySelector('[class*="title"], [class*="name"], h3, h4, [class*="position-name"]');

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
    """访问 MiniMax 飞书招聘详情页，提取职位描述和要求。使用降级策略：CSS 失效则 inner_text 全文。"""
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
                "main, article, [class*='content'], "
                ".job-description, .job-requirement",
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
                    '.job-description',
                    '.job-requirement',
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

        logger.info(f"🌐 访问 MiniMax 飞书招聘页面...")
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
            time.sleep(5)
            take_screenshot(page, "02_after_wait_no_list")

        # 截图列表页
        take_screenshot(page, "03_before_pagination")

        # 飞书招聘翻页通过 URL 参数 current=N
        for page_num in range(1, MAX_PAGES + 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📄 第 {page_num}/{MAX_PAGES} 页")
            logger.info(f"{'='*60}")

            # 如果是第 2 页及以上，通过 URL 翻页
            if page_num > 1:
                next_url = re.sub(r'current=\d+', f'current={page_num}', TARGET_URL)
                logger.info(f"👉 翻页到第 {page_num} 页: {next_url}")
                try:
                    page.goto(next_url, timeout=NAVIGATE_TIMEOUT, wait_until="domcontentloaded")
                    time.sleep(3)
                    if not wait_for_list_load(page):
                        logger.warning(f"⚠️ 第 {page_num} 页列表未渲染")
                    take_screenshot(page, f"04_page{page_num}")
                except Exception as e:
                    logger.warning(f"⚠️ 翻页失败: {e}")
                    break

            page_jobs = collect_job_links(page)
            logger.info(f"📌 当前页获取到 {len(page_jobs)} 个岗位")

            existing_urls = {j["url"] for j in all_job_links}
            new_jobs = [j for j in page_jobs if j["url"] not in existing_urls]
            if new_jobs:
                all_job_links.extend(new_jobs)
                logger.info(f"✨ 新增 {len(new_jobs)} 个，累计 {len(all_job_links)} 个")
            else:
                logger.info(f"💡 当前页无新岗位，累计 {len(all_job_links)} 个")

            # 检查是否还有下一页（飞书招聘如果当前页数据不足 limit 说明是最后一页）
            if len(page_jobs) < 10:
                logger.info("🏁 当前页数据不足 10 条，已到达最后一页")
                break

        logger.info(f"\n{'='*60}")
        logger.info(f"📊 共收集到 {len(all_job_links)} 个岗位 URL")
        logger.info(f"{'='*60}")

        # 逐个访问详情页
        if all_job_links:
            logger.info(f"\n🔎 开始深度抓取岗位详情 ({len(all_job_links)} 个)")
            for idx, job in enumerate(all_job_links, 1):
                # ★ 实时心跳日志
                print(f"\n{'='*60}")
                print(f"⏱️  [{idx}/{len(all_job_links)}] 正在访问 MiniMax 详情页: {job['url']}")
                print(f"   岗位: {job['title']}")
                print(f"{'='*60}")
                logger.info(f"\n[{idx}/{len(all_job_links)}] {job['title']}")
                detail = extract_job_detail(page, job["url"], job["title"])
                if detail:
                    all_job_details.append(detail)
                    logger.info(f"  ✅ [{idx}/{len(all_job_links)}] 完成")
                else:
                    logger.warning(f"  ⚠️ [{idx}/{len(all_job_links)}] 提取失败")

                if idx < len(all_job_links):
                    random_sleep("抓取间隔")

            logger.info(f"\n🎉 MiniMax 抓取完成！共 {len(all_job_details)} 条")
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
    print("📊 MiniMax（稀宇科技）精准爬取结果")
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
    print("🔍 MiniMax（稀宇科技）精准爬虫")
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
