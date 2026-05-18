#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
spider_liepin.py — 猎聘网爬虫（多平台爬虫工厂 · 猎聘适配器）

基于 DrissionPage 搭建，输出标准化 JobItem 格式。
猎聘特点：
  - 列表页和详情页没有严格的加密 API，可直接通过 DOM 提取
  - 搜索 URL 结构：https://www.liepin.com/zhaopin/?key={keyword}
  - 分页通过 URL 参数 ?curPage={n} 控制

用法:
  python spider_liepin.py                          # 独立运行
  python -c "from spider_liepin import run; jobs = run()"  # 作为模块调用
"""

import sys
import io
import json
import random
import time
import logging
import signal
import atexit
import re
import urllib.parse
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import ContextLostError

from job_model import JobItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("spider_liepin")

# ==========================================
# 🎯 抓取条件控制面板（默认值，可通过 run() 参数覆盖）
# ==========================================
KEYWORD = "AI产品经理"
CITY_CODE = "0"  # 全国

SLEEP_MIN, SLEEP_MAX = 3, 6
PAGE_SLEEP_MIN, PAGE_SLEEP_MAX = 5, 10
MAX_PAGES = 5

# ==========================================
# 📂 全局去重：基于纯净版 URL 的 history_jobs.json
# ==========================================
HISTORY_JOBS_FILE = Path(__file__).parent / "history_jobs.json"


def _clean_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    clean = raw_url.split("?")[0].split("#")[0]
    return clean.strip()


def _load_history_jobs() -> set[str]:
    if not HISTORY_JOBS_FILE.exists():
        logger.info("history_jobs.json 不存在，初始化为空集合")
        return set()
    try:
        with open(HISTORY_JOBS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            result = set(data)
            logger.info("history_jobs.json loaded: %d 条已处理记录", len(result))
            return result
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("读取 history_jobs.json 失败: %s，重置为空集合", e)
    return set()


def _save_history_job(clean_url: str) -> None:
    if not clean_url:
        return
    try:
        existing = []
        if HISTORY_JOBS_FILE.exists():
            with open(HISTORY_JOBS_FILE, encoding="utf-8") as f:
                existing = json.load(f)
        if not isinstance(existing, list):
            existing = []
        if clean_url not in existing:
            existing.append(clean_url)
        with open(HISTORY_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("写入 history_jobs.json 失败: %s", e)


def init_browser(max_retries: int = 3) -> ChromiumPage:
    """
    初始化浏览器，带延迟防御和自动重试机制。

    Args:
        max_retries: 浏览器初始化失败时的最大重试次数（默认 3 次）
    """
    co = ChromiumOptions()
    co.set_local_port(9334)
    co.set_user_data_path('./.chrome_profile_liepin')
    co.headless(False)
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--disable-popup-blocking')
    co.set_argument('--disable-infobars')
    co.set_argument('--hide-crash-restore-bubble')
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--lang=zh-CN")
    co.set_pref("excludeSwitches", ["enable-automation"])
    co.set_pref("useAutomationExtension", False)

    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                logger.info(f"🔄 浏览器初始化重试第 {attempt}/{max_retries} 次...")
                time.sleep(3 * attempt)

            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=15, page_load=30, script=15)
            logger.info("浏览器已启动 (port=9334, user_data_path=./.chrome_profile_liepin)")

            # ★ 延迟防御：初始化后强制等待 5 秒
            logger.info("⏳ 等待 5 秒让浏览器稳定...")
            time.sleep(5)

            # 导航到猎聘首页
            page.get("https://www.liepin.com/")
            time.sleep(3)

            # ★ 登录检测，用 try-catch 包裹
            try:
                is_logged_in = page.run_js("""
                    const bodyText = document.body?.innerText || '';
                    const loggedInIndicators = ['退出', '退出登录', '我的猎聘', '我的简历', '我的收藏'];
                    const hasLoggedInText = loggedInIndicators.some(t => bodyText.includes(t));
                    const loggedOutIndicators = ['登录/注册', '免费注册'];
                    const hasLoggedOutText = loggedOutIndicators.some(t => bodyText.includes(t));
                    const loginLinks = document.querySelectorAll('a[href*="login"], a[href*="passport"], [class*="not-login"]');
                    const hasLoginLinks = loginLinks.length > 0;
                    if (hasLoggedInText && !hasLoggedOutText) return true;
                    if (hasLoggedOutText || hasLoginLinks) return false;
                    return false;
                """)
            except (ContextLostError, Exception) as js_e:
                logger.warning(f"⚠️ 登录检测 JS 执行失败 (attempt {attempt}): {js_e}")
                time.sleep(3)
                page.get("https://www.liepin.com/")
                time.sleep(3)
                is_logged_in = page.run_js("return document.body?.innerText?.includes('退出') || false;")

            if is_logged_in:
                logger.info("检测到已登录状态（本地 Cookie 有效），跳过扫码等待，直接开始抓取...")
            else:
                logger.warning("检测到未登录状态，需要手动扫码登录")
                print("\n" + "=" * 60)
                print("🔐 请在弹出的浏览器中手动扫码登录猎聘")
                print("   浏览器窗口已打开，请完成登录操作")
                print("   登录成功后，请在终端按回车键继续...")
                print("=" * 60 + "\n")
                try:
                    input("按回车键继续...")
                    logger.info("用户已确认登录完成，继续执行")
                except EOFError:
                    logger.warning("非交互式终端，跳过登录等待（可能无法获取数据）")

            return page

        except (ContextLostError, Exception) as e:
            last_exception = e
            logger.error(f"❌ 浏览器初始化失败 (attempt {attempt}/{max_retries}): {e}")
            try:
                page.quit()
            except Exception:
                pass
            if attempt < max_retries:
                wait_time = 5 * attempt
                logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ 浏览器初始化已重试 {max_retries} 次，全部失败")
                raise last_exception


def _smooth_scroll_to_bottom(page: ChromiumPage) -> None:
    logger.info("开始平滑滚动到页面底部...")
    max_steps = 20
    for step in range(1, max_steps + 1):
        try:
            page.scroll.down(500)
        except Exception as e:
            logger.warning("滚动中途页面上下文丢失（第 %d 步）: %s", step, e)
            break
        pause = random.uniform(0.5, 1.0)
        time.sleep(pause)
        try:
            if page.scroll.is_at_bottom():
                logger.info("已滚动到页面底部（第 %d 步）", step)
                break
        except Exception:
            continue
    logger.info("平滑滚动完成")


def _parse_job_card(card) -> dict:
    try:
        result = card.run_js("""
            const card = this;
            const titleEl = card.querySelector('.ellipsis-1[title]');
            const jobName = titleEl ? (titleEl.getAttribute('title') || titleEl.innerText.trim()) : '';
            const companyContainer = card.querySelector('[data-nick="job-detail-company-info"]');
            let brandName = '';
            if (companyContainer) {
                const companyEl = companyContainer.querySelector('.ellipsis-1');
                brandName = companyEl ? companyEl.innerText.trim() : companyContainer.innerText.trim();
            }
            const linkEl = card.querySelector('a[data-nick="job-detail-job-info"]');
            const href = linkEl ? linkEl.getAttribute('href') : '';
            const idMatch = href ? href.match(/\\/job\\/(\\d+)\\.s?html/) : null;
            const encryptJobId = idMatch ? idMatch[1] : '';
            let salaryDesc = '';
            let cityName = '';
            if (linkEl) {
                const linkText = linkEl.innerText || '';
                const salaryMatch = linkText.match(/(\\d+[kK]-\\d+[kK]|\\d+[kK]·\\d+薪|\\d+[kK]|薪资面议)/);
                if (salaryMatch) salaryDesc = salaryMatch[1].trim();
                const cityMatch = linkText.match(/【(.+?)】/);
                if (cityMatch) cityName = cityMatch[1].trim();
            }
            return { jobName, brandName, salaryDesc, cityName, href, encryptJobId };
        """)
        if not result or not result.get("jobName"):
            return {}
        href = result.get("href", "")
        if href and not href.startswith('http'):
            href = f"https://www.liepin.com{href}"
        return {
            "jobName": result.get("jobName", ""),
            "brandName": result.get("brandName", ""),
            "salaryDesc": result.get("salaryDesc", ""),
            "cityName": result.get("cityName", ""),
            "url": href,
            "encryptJobId": result.get("encryptJobId", ""),
        }
    except Exception as e:
        logger.warning("解析岗位卡片失败: %s", e)
        return {}


def _parse_liepin_api_response(body) -> list[dict]:
    jobs = []
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return jobs
    if not isinstance(body, dict):
        return jobs
    data = body.get("data", {}) or body.get("content", {})
    if isinstance(data, dict):
        job_list = data.get("list", []) or data.get("data", {}).get("list", []) or data.get("jobList", [])
    else:
        job_list = []
    if not job_list:
        job_list = body.get("list", [])
    for item in job_list:
        job_name = item.get("jobName", "") or item.get("name", "") or item.get("title", "")
        company = item.get("companyName", "") or item.get("company", "") or item.get("brandName", "")
        salary = item.get("salary", "") or item.get("salaryDesc", "") or ""
        city = item.get("cityName", "") or item.get("city", "") or item.get("area", "") or ""
        job_id = item.get("jobId", "") or item.get("id", "") or item.get("encryptJobId", "")
        url = f"https://www.liepin.com/job/{job_id}.html" if job_id else ""
        jobs.append({
            "jobName": job_name, "brandName": company,
            "salaryDesc": salary, "cityName": city,
            "url": url, "encryptJobId": job_id,
        })
    return jobs


def _collect_liepin_api_jobs(page: ChromiumPage, listen_timeout: int = 10) -> list[dict]:
    collected = []
    deadline = time.time() + listen_timeout
    while time.time() < deadline:
        try:
            remaining = max(0.5, deadline - time.time())
            r = page.listen.wait(timeout=remaining)
            if r and r.response and r.response.body:
                parsed = _parse_liepin_api_response(r.response.body)
                if parsed:
                    logger.info("猎聘 API 拦截到 %d 条岗位", len(parsed))
                    collected.extend(parsed)
        except Exception:
            continue
    return collected


def _dom_extract_jobs(page: ChromiumPage) -> list[dict]:
    jobs = []
    try:
        cards_data = page.run_js("""
            const cards = document.querySelectorAll('.job-card-pc-container');
            return Array.from(cards).slice(0, 80).map(card => {
                const titleEl = card.querySelector('.ellipsis-1[title]');
                const jobName = titleEl ? (titleEl.getAttribute('title') || titleEl.innerText.trim()) : '';
                const companyContainer = card.querySelector('[data-nick="job-detail-company-info"]');
                let brandName = '';
                if (companyContainer) {
                    const companyEl = companyContainer.querySelector('.ellipsis-1');
                    brandName = companyEl ? companyEl.innerText.trim() : companyContainer.innerText.trim();
                }
                const linkEl = card.querySelector('a[data-nick="job-detail-job-info"]');
                const href = linkEl ? linkEl.getAttribute('href') : '';
                const idMatch = href ? href.match(/\\/job\\/(\\d+)\\.s?html/) : null;
                const encryptJobId = idMatch ? idMatch[1] : '';
                let salaryDesc = '';
                let cityName = '';
                if (linkEl) {
                    const linkText = linkEl.innerText || '';
                    const salaryMatch = linkText.match(/(\\d+[kK]-\\d+[kK]|\\d+[kK]·\\d+薪|\\d+[kK]|薪资面议)/);
                    if (salaryMatch) salaryDesc = salaryMatch[1].trim();
                    const cityMatch = linkText.match(/【(.+?)】/);
                    if (cityMatch) cityName = cityMatch[1].trim();
                }
                return { jobName, brandName, salaryDesc, cityName, href, encryptJobId };
            });
        """)
        if cards_data:
            for raw in cards_data:
                if raw.get("jobName") and raw.get("brandName"):
                    href = raw.get("href", "")
                    if href and not href.startswith('http'):
                        href = f"https://www.liepin.com{href}"
                    jobs.append({
                        "jobName": raw.get("jobName", ""),
                        "brandName": raw.get("brandName", ""),
                        "salaryDesc": raw.get("salaryDesc", ""),
                        "cityName": raw.get("cityName", ""),
                        "url": href,
                        "encryptJobId": raw.get("encryptJobId", ""),
                    })
            logger.info("DOM 解析到 %d 条岗位", len(jobs))
        else:
            logger.warning("DOM 解析返回空")
    except Exception as e:
        logger.warning("DOM 提取失败: %s", e)
    return jobs


def extract_list_jobs(page: ChromiumPage, keyword: str = "", max_pages: int = 0) -> list[JobItem]:
    kw = keyword or KEYWORD
    mp = max_pages if max_pages is not None and max_pages > 0 else MAX_PAGES
    encoded_kw = urllib.parse.quote(kw)
    search_url = (
        f"https://www.liepin.com/zhaopin/"
        f"?city=410&dq=410&pubTime=30&currentPage={{page}}"
        f"&pageSize=40&key={encoded_kw}&suggestTag=&workYearCode=0"
        f"&compId=&compName=&compTag=&industry=&salaryCode="
        f"&jobKind=&compScale=&compKind=&compStage=&eduLevel="
        f"&otherCity=&scene=condition&sfrom=search_job_pc"
    )
    logger.info("正在访问猎聘搜索列表页（完整 URL）")
    logger.debug("URL: %s", search_url.format(page=0))

    seen = set()
    all_unique: list[JobItem] = []

    for current_page in range(mp):
        page_num = current_page + 1
        logger.info("===== 猎聘第 %d 页 =====", page_num)
        current_url = search_url.format(page=current_page)
        page.get(current_url)
        page_load_sleep = random.uniform(3, 6)
        logger.info("等待页面加载 %.1f 秒...", page_load_sleep)
        time.sleep(page_load_sleep)

        # API 拦截优先
        api_jobs = []
        try:
            page.listen.start("liepin.com/api/")
            api_jobs = _collect_liepin_api_jobs(page, listen_timeout=8)
            page.listen.stop()
        except Exception as e:
            logger.warning("第 %d 页 API 拦截失败: %s", page_num, e)
            try:
                page.listen.stop()
            except Exception:
                pass

        if api_jobs:
            logger.info("第 %d 页 API 获取到 %d 条岗位", page_num, len(api_jobs))
            for raw in api_jobs:
                job_name = raw.get("jobName", "")
                company = raw.get("brandName", "")
                url = raw.get("url", "")
                if not job_name or not company:
                    continue
                dedup_key = url or job_name + company
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                all_unique.append(JobItem(
                    platform="猎聘", job_name=job_name, company=company,
                    salary=raw.get("salaryDesc", ""), city=raw.get("cityName", ""),
                    url=url, platform_job_id=raw.get("encryptJobId", ""),
                ))
        else:
            # DOM 兜底
            logger.info("第 %d 页 API 未获取到数据，回退到 DOM 解析...", page_num)
            try:
                page.wait.ele_displayed('.job-card-pc-container', timeout=10)
            except Exception:
                try:
                    page.wait.ele_displayed('.job-detail-box', timeout=8)
                except Exception:
                    try:
                        page.wait.ele_displayed('.job-list-box', timeout=5)
                    except Exception:
                        pass
            dom_jobs = _dom_extract_jobs(page)
            for raw in dom_jobs:
                job_name = raw.get("jobName", "")
                company = raw.get("brandName", "")
                url = raw.get("url", "")
                if not job_name or not company:
                    continue
                dedup_key = url or job_name + company
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                all_unique.append(JobItem(
                    platform="猎聘", job_name=job_name, company=company,
                    salary=raw.get("salaryDesc", ""), city=raw.get("cityName", ""),
                    url=url, platform_job_id=raw.get("encryptJobId", ""),
                ))
            logger.info("第 %d 页 DOM 提取到 %d 条岗位（累计 %d 条）", page_num, len(dom_jobs), len(all_unique))

        page_count = len(api_jobs) if api_jobs else (len(dom_jobs) if 'dom_jobs' in dir() else 0)
        if page_count == 0:
            logger.info("第 %d 页未获取到任何岗位，判定已到尾页，翻页结束", page_num)
            break

        if current_page < mp - 1:
            page_sleep = random.uniform(PAGE_SLEEP_MIN, PAGE_SLEEP_MAX)
            logger.info("准备翻到第 %d 页，等待 %.1f 秒...", page_num + 1, page_sleep)
            time.sleep(page_sleep)

    if not all_unique:
        logger.error("猎聘未获取到任何岗位数据，截取当前页面截图以供排查")
        try:
            screenshot_path = str(Path(__file__).parent / "error_page_liepin.png")
            page.get_screenshot(path=screenshot_path)
            logger.info("截图已保存: %s", screenshot_path)
        except Exception as e:
            logger.warning("截图保存失败: %s", e)

    logger.info("猎聘全部页面共获取到 %d 条岗位（去重后）", len(all_unique))
    return all_unique


def _check_risk_control(page: ChromiumPage, detail_url: str) -> bool:
    risk_keywords = ['账号行为异常', '短信验证码', '安全中心', '行为异常', '验证码']
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            body_text = page.run_js("return (document.body?.innerText || '').substring(0, 500);")
        except Exception:
            body_text = ""
        hit_keywords = [kw for kw in risk_keywords if kw in body_text]
        if not hit_keywords:
            return True
        risk_msg = "、".join(hit_keywords)
        logger.error("🚨 触发猎聘风控！检测到关键词: %s", risk_msg)
        print("\n" + "=" * 70)
        print("\033[91m" + "🚨 [警告] 触发猎聘风控！请在弹出的浏览器中手动完成短信验证！" + "\033[0m")
        print(f"\033[91m   检测到: {risk_msg}\033[0m")
        print(f"   当前岗位: {detail_url}")
        print(f"   尝试 {attempt}/{max_retries}")
        print("=" * 70)
        try:
            input("完成短信验证并看到正常页面后，请按回车键继续...")
        except EOFError:
            logger.warning("非交互式终端，跳过风控等待")
            return False
        try:
            page.get(detail_url)
            time.sleep(random.uniform(3, 6))
        except Exception as e:
            logger.warning("风控重试访问详情页失败: %s", e)
    logger.error("❌ 多次重试后仍无法通过风控验证: %s", detail_url)
    return False


def _check_login_redirect(page: ChromiumPage, expected_job_url: str) -> bool:
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        current_url = page.url.lower()
        page_title = page.title.lower()
        is_login_page = (
            'wow.liepin.com' in current_url or 'login' in current_url
            or 'passport' in current_url or '登录' in page_title or 'login' in page_title
        )
        if not is_login_page:
            try:
                body_text = page.run_js("return (document.body?.innerText || '').substring(0, 200);")
                if len(body_text) > 50 and '登录' not in body_text[:100]:
                    return True
            except Exception:
                pass
        logger.warning("⚠️ 检测到登录重定向（尝试 %d/%d）", attempt, max_retries)
        print("\n" + "=" * 60)
        print("⚠️ 触发反爬/登录墙！请在浏览器中扫码登录猎聘")
        print("   登录完成后，在终端按回车键继续...")
        print("=" * 60)
        try:
            input("按回车键继续...")
        except EOFError:
            logger.warning("非交互式终端，跳过登录重定向等待")
            return False
        try:
            page.get(expected_job_url)
            time.sleep(random.uniform(3, 6))
        except Exception as e:
            logger.warning("重试访问详情页失败: %s", e)
    logger.error("❌ 多次重试后仍无法访问详情页: %s", expected_job_url)
    return False


def _extract_full_jd(page: ChromiumPage) -> str:
    jd = page.run_js("""
        const selectors = [
            '.job-intro', '.job-item-title', '[data-selector="job-intro-content"]',
            '.job-description', '.job-detail-description',
            '.job-detail-section', '.job-detail-content', '.job-detail-info',
            '.job-requirements', '.job-responsibility',
            '.position-detail', '.position-description',
            '[class*="job-detail"]', '[class*="job-description"]',
            '[class*="job-intro"]', '[class*="position-detail"]',
            '[class*="job-require"]', '[class*="job-respons"]',
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) { const text = el.innerText.trim(); if (text.length > 80) return text; }
        }
        const main = document.querySelector('main') || document.querySelector('.content-wrap') || document.querySelector('[class*="content"]');
        if (main) { const text = main.innerText.trim(); if (text.length > 100) return text; }
        return '';
    """)
    return jd.strip() if jd else ""


def fetch_jd(page: ChromiumPage, job: JobItem) -> str:
    detail_url = job.url
    if not detail_url:
        logger.warning("岗位 URL 为空，无法获取 JD")
        return ""
    try:
        jitter_sleep = random.uniform(6.5, 15.3)
        logger.info("🛡️ 拟人化休眠 %.1f 秒（模拟浏览列表后点击详情）...", jitter_sleep)
        time.sleep(jitter_sleep)
        page.get(detail_url)
        if not _check_risk_control(page, detail_url):
            logger.error("无法通过风控验证，跳过 JD 获取: %s", detail_url)
            return ""
        if not _check_login_redirect(page, detail_url):
            logger.error("无法访问详情页（登录墙），跳过 JD 获取: %s", detail_url)
            return ""
        scroll_distance = random.randint(300, 800)
        try:
            page.scroll.down(scroll_distance)
            logger.info("🛡️ 模拟浏览: 向下滚动 %d 像素", scroll_distance)
        except Exception as e:
            logger.warning("模拟滚动失败（不影响后续提取）: %s", e)
        reading_sleep = random.uniform(1.5, 3.5)
        logger.info("🛡️ 模拟浏览: 阅读页面 %.1f 秒...", reading_sleep)
        time.sleep(reading_sleep)
        jd = _extract_full_jd(page)
        if jd and len(jd) >= 80:
            logger.info("✅ 猎聘详情页获取到完整 JD（%d 字符）", len(jd))
            return jd
        else:
            logger.warning("JD 内容过短（%d 字符），等待后重试...", len(jd) if jd else 0)
            time.sleep(3)
            jd = _extract_full_jd(page)
            if jd and len(jd) >= 80:
                logger.info("✅ 重试后获取到完整 JD（%d 字符）", len(jd))
                return jd
            logger.error("❌ 详情页 JD 提取失败（内容过短），URL: %s", detail_url)
            return ""
    except Exception as e:
        logger.error("猎聘详情页抓取异常: %s - %s", detail_url, e)
        return ""


def process_jobs(page: ChromiumPage, jobs: list[JobItem]) -> None:
    import ai_matcher
    import notion_sync

    history_jobs = _load_history_jobs()
    skipped_count = 0
    processed_count = 0

    for idx, job in enumerate(jobs, 1):
        job_name = job.job_name
        company = job.company
        salary = job.salary
        city = job.city
        url = job.url

        if not job_name or not company:
            logger.warning("[%d/%d] 跳过无效岗位数据: %s", idx, len(jobs), job)
            continue

        clean_url = _clean_url(url) if url else ""
        if clean_url and clean_url in history_jobs:
            logger.info("[%d/%d] 岗位已处理过，跳过抓取: %s", idx, len(jobs), clean_url)
            skipped_count += 1
            continue

        print(f"\n{'='*60}")
        print(f"[{idx}/{len(jobs)}] 处理岗位: {job_name} @ {company}")
        print(f"    薪资: {salary} | 地点: {city} | 平台: {job.platform}")
        print(f"    URL: {url}")
        print(f"{'='*60}")

        try:
            jd = fetch_jd(page, job) if url else ""
            if not jd or len(jd) < 80:
                logger.error("❌ [%d/%d] 详情页 JD 提取失败（内容过短），跳过该岗位: %s",
                             idx, len(jobs), clean_url or url)
                try:
                    inbox_path = Path(__file__).parent / "failed_jobs_inbox.md"
                    timestamp = time.strftime("%Y-%m-%d %H:%M")
                    line = (f"- [ ] **[{timestamp}]** 岗位：{job_name} (公司：{company}) "
                            f"- [查看岗位]({url}) - JD提取失败(内容过短)\n")
                    with open(inbox_path, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception:
                    pass
                continue

            logger.info("调用 AI 评估: %s - %s", job_name, company)
            evaluation = ai_matcher.evaluate_job(
                title=job_name, company=company, salary=salary,
                location=city, platform=job.platform, jd_summary=jd,
            )
            score = evaluation.get("score", 0)
            summary = evaluation.get("summary", "")
            match_reasons = evaluation.get("match_reasons", [])
            mismatch_reasons = evaluation.get("mismatch_reasons", [])
            logger.info("AI 评估完成: 评分 %d/100 — %s", score, summary[:60] if summary else "无总结")

            logger.info("同步到 Notion: %s - %s", job_name, company)
            notion_sync.sync_job(
                title=job_name, company=company, platform=job.platform,
                url=url, location=city, salary_range=salary,
                jd_summary=jd[:2000] if jd else "",
                match_score=score, match_reasons=match_reasons,
                mismatch_reasons=mismatch_reasons,
                status="新发现", notes=summary,
            )

            if clean_url:
                _save_history_job(clean_url)
                history_jobs.add(clean_url)
                processed_count += 1

        except Exception as e:
            logger.error("处理岗位失败 [%s - %s]: %s", job_name, company, e, exc_info=True)
            try:
                inbox_path = Path(__file__).parent / "failed_jobs_inbox.md"
                timestamp = time.strftime("%Y-%m-%d %H:%M")
                safe_name = job_name or "未知"
                safe_company = company or "未知"
                safe_url = url or "未知"
                brief = str(e).split("\n")[0][:80]
                line = f"- [ ] **[{timestamp}]** 岗位：{safe_name} (公司：{safe_company}) - [查看岗位]({safe_url}) - 报错: {brief}\n"
                with open(inbox_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as write_err:
                logger.warning("写入错题本失败: %s", write_err)

        if idx < len(jobs):
            sleep_time = random.uniform(SLEEP_MIN, SLEEP_MAX)
            logger.info("防反爬休眠 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)

    logger.info("猎聘处理完成: 成功 %d 条，跳过 %d 条（已处理）", processed_count, skipped_count)


_browser_page: ChromiumPage | None = None


def _cleanup_browser():
    global _browser_page
    if _browser_page is not None:
        try:
            _browser_page.quit()
            logger.info("✅ 浏览器资源已释放")
        except Exception:
            pass
        finally:
            _browser_page = None


def _signal_handler(signum, frame):
    logger.warning("接收到信号 %d，正在强制释放浏览器资源...", signum)
    _cleanup_browser()
    sys.exit(128 + signum)


def run(keyword: str = "", max_pages: int = 0) -> list[JobItem]:
    global _browser_page
    atexit.register(_cleanup_browser)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    page = None
    try:
        page = init_browser()
        _browser_page = page
        jobs = extract_list_jobs(page, keyword=keyword, max_pages=max_pages)
        if not jobs:
            logger.warning("猎聘未获取到任何岗位，退出")
            return []
        print(f"\n📋 猎聘共获取到 {len(jobs)} 个岗位，开始逐个处理...")
        process_jobs(page, jobs)
        print(f"\n{'='*60}")
        print("✅ 猎聘抓取完成！")
        print(f"{'='*60}")
        return jobs
    except KeyboardInterrupt:
        logger.warning("用户手动中断，正在清理...")
    except Exception as e:
        logger.error("脚本运行失败: %s", e, exc_info=True)
    finally:
        _cleanup_browser()
    return []


def main():
    run()


if __name__ == "__main__":
    main()
