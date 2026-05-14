#!/usr/bin/env python3
"""
xhs_simple.py — 小红书招聘抓取（最粗暴版）
使用 Playwright 访问 job.xiaohongshu.com 招聘站点。
不解析职责/要求，直接抓取详情页最大内容容器的全部文字。
"""

import sys, io, json, time, logging, os, re

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("xhs_simple")

from playwright.sync_api import sync_playwright

# ── 配置 ──
TARGET_URL = "https://job.xiaohongshu.com/social/position?positionName=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86"
OUTPUT_FILE = "data/xhs_simple_jobs.json"
MAX_SCROLLS = 8
SCROLL_WAIT = 2.0


def init_browser():
    """启动 Playwright 浏览器（有界面）"""
    logger.info("🚀 启动 Playwright 浏览器...")
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=False,
        args=[
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
    # Stealth 反检测
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
    """)
    page = context.new_page()
    page.set_default_timeout(60000)
    logger.info("✅ 浏览器已启动")
    return p, browser, context, page


def scroll_to_load_more(page) -> int:
    """滚动加载更多岗位"""
    before = page.evaluate("""
        () => document.querySelectorAll('a[href*="/social/position/"]').length
    """) or 0

    doc_height = page.evaluate("document.body.scrollHeight") or 8000
    for step in range(1, 6):
        page.evaluate(f"window.scrollTo(0, {int(step * doc_height / 5)})")
        time.sleep(0.5)
    time.sleep(SCROLL_WAIT)

    after = page.evaluate("""
        () => document.querySelectorAll('a[href*="/social/position/"]').length
    """) or 0

    new_count = after - before
    logger.info(f"📜 滚动加载: +{new_count} 个 (总计 {after})")
    return new_count


def collect_job_links(page) -> list[dict]:
    """收集所有岗位链接"""
    jobs = page.evaluate("""
        () => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/social/position/"]');
            links.forEach(a => {
                const url = a.href;
                if (url === 'https://job.xiaohongshu.com/social/position') return;
                if (url === 'https://job.xiaohongshu.com/') return;
                const fullText = a.innerText.trim();
                const lines = fullText.split(String.fromCharCode(10));
                const title = lines[0].trim();
                if (url && title && !results.find(r => r.url === url)) {
                    results.push({ url, title });
                }
            });
            return results;
        }
    """) or []
    logger.info(f"📋 收集到 {len(jobs)} 个岗位链接")
    return jobs


def extract_full_jd(page, url: str, title: str) -> dict:
    """
    最粗暴的详情页抓取：
    不解析职责/要求，直接找最大内容容器，抓全部文字。
    """
    logger.info(f"🔍 进入详情页: {title}")
    result = {
        "title": title,
        "url": url,
        "full_jd": "",
        "source": "xiaohongshu",
        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)

        # 粗暴抓取：找最大的内容容器
        full_text = page.evaluate("""
            () => {
                // 策略1: 尝试已知的 JD 容器选择器
                const selectors = [
                    '.job-detail', '.position-detail', '.jd-content',
                    '[class*="job-detail"]', '[class*="position-detail"]',
                    '[class*="jd-content"]', '.detail-content',
                    '.content-wrapper', 'main', 'article',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 50) {
                        return el.innerText.trim();
                    }
                }

                // 策略2: 找页面中文本最长的 div/section
                const candidates = document.querySelectorAll('div[class], section[class]');
                let maxLen = 0;
                let bestText = '';
                for (const el of candidates) {
                    const text = el.innerText.trim();
                    const len = text.length;
                    if (len > maxLen && len < 100000) {
                        maxLen = len;
                        bestText = text;
                    }
                }
                if (bestText) return bestText;

                // 策略3: 整个 body
                return document.body.innerText.trim();
            }
        """) or ""

        if full_text:
            # 清洗：去掉多余空白和乱码
            full_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', full_text)
            full_text = re.sub(r'[ \t]+', ' ', full_text)
            full_text = re.sub(r'\n{3,}', '\n\n', full_text)
            result["full_jd"] = full_text.strip()
            logger.info(f"  ✅ 抓取完成: {len(full_text)} 字符")
        else:
            logger.warning(f"  ⚠️ 未获取到内容")

    except Exception as e:
        logger.error(f"  ❌ 详情页抓取失败: {e}")

    return result


def main():
    print("\n" + "=" * 60)
    print("🔍 小红书招聘粗暴抓取")
    print("=" * 60)
    print(f"  目标: {TARGET_URL}")
    print(f"  输出: {OUTPUT_FILE}")
    print("=" * 60)

    p = browser = context = page = None
    all_results = []

    try:
        p, browser, context, page = init_browser()

        # 访问招聘列表页
        logger.info("🌐 访问小红书招聘页面...")
        page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
        time.sleep(4)

        # 滚动加载更多
        logger.info("📜 开始滚动加载...")
        for i in range(1, MAX_SCROLLS + 1):
            logger.info(f"  第 {i}/{MAX_SCROLLS} 次滚动")
            new_count = scroll_to_load_more(page)
            if new_count == 0 and i > 2:
                logger.info("🏁 无新内容，停止滚动")
                break

        # 收集链接
        job_links = collect_job_links(page)
        logger.info(f"📊 共 {len(job_links)} 个岗位")

        # 只抓前 10 条
        job_links = job_links[:10]
        logger.info(f"🎯 将抓取前 {len(job_links)} 条")

        # 逐个访问详情页
        for idx, job in enumerate(job_links, 1):
            logger.info(f"\n[{idx}/{len(job_links)}] {job['title']}")
            detail = extract_full_jd(page, job["url"], job["title"])
            all_results.append(detail)
            logger.info(f"  ✅ [{idx}/{len(job_links)}] 完成")
            if idx < len(job_links):
                time.sleep(2)

        logger.info(f"\n🎉 抓取完成！共 {len(all_results)} 条")

    except Exception as e:
        logger.error(f"❌ 脚本异常: {e}", exc_info=True)
    finally:
        if page:
            try: context.close()
            except: pass
        if browser:
            try: browser.close()
            except: pass
        if p:
            try: p.stop()
            except: pass
        logger.info("✅ 浏览器已关闭")

    # 保存结果
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存到 {OUTPUT_FILE}")

    # 打印摘要
    print(f"\n{'='*60}")
    print(f"📊 抓取摘要")
    print(f"{'='*60}")
    for idx, job in enumerate(all_results, 1):
        jd_len = len(job.get("full_jd", ""))
        status = "✅" if jd_len > 0 else "❌"
        print(f"  {status} [{idx:2d}] {job['title']} ({jd_len} 字符)")

    return all_results


if __name__ == "__main__":
    main()
