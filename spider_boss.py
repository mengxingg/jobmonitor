#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
spider_boss.py — BOSS直聘爬虫（多平台爬虫工厂 · Boss 适配器）

基于 scraper_drission.py 重构，输出标准化 JobItem 格式。
下游 ai_matcher / notion_sync 不关心数据来源。

用法:
  python spider_boss.py                          # 独立运行
  python -c "from spider_boss import run; jobs = run()"  # 作为模块调用
"""

import sys
import io
import json
import random
import time
import logging
import signal
import atexit
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from DrissionPage import ChromiumPage, ChromiumOptions

from job_model import JobItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("spider_boss")

# ==========================================
# 🎯 抓取条件控制面板
# ==========================================
KEYWORD = "AI产品经理"
CITY_CODE = "100010000"       # 全国
EXPERIENCE = ""               # 不限
DEGREE = ""                   # 学历不限（移除 degree=203 限制，获取全量岗位）
SEARCH_URL = f"https://www.zhipin.com/web/geek/job?query={KEYWORD}&city={CITY_CODE}&experience={EXPERIENCE}&degree={DEGREE}"

LISTEN_TIMEOUT = 8
DETAIL_TIMEOUT = 3
SLEEP_MIN, SLEEP_MAX = 3, 6
PAGE_SLEEP_MIN, PAGE_SLEEP_MAX = 5, 10
BLACKLIST_KEYWORDS = ['实习', '校招', '应届', '26届', '27届']

# ── 学历前置过滤规则（保护大模型 Token） ──
# 低于本科的学历关键词，命中则直接丢弃
LOW_EDUCATION_KEYWORDS = ['大专', '中专', '高中', '初中', '中技']
# 允许放行的学历关键词
ALLOWED_EDUCATION_KEYWORDS = ['本科', '硕士', '博士', '学历不限', '不限']

PROCESSED_JOBS_FILE = Path(__file__).parent / "processed_jobs.txt"


def _load_processed_urls() -> set[str]:
    if not PROCESSED_JOBS_FILE.exists():
        return set()
    urls: set[str] = set()
    with open(PROCESSED_JOBS_FILE, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                urls.add(stripped)
    logger.info("本地缓存 loaded: %d 条已处理 URL", len(urls))
    return urls


def _mark_url_processed(url: str) -> None:
    with open(PROCESSED_JOBS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")


def init_browser() -> ChromiumPage:
    """初始化浏览器（有界面模式，Boss 直聘对无头模式风控极严）"""
    co = ChromiumOptions()
    co.set_local_port(9333)
    co.set_user_data_path('./.chrome_profile')
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

    try:
        page = ChromiumPage(addr_or_opts=co)
        page.set.timeouts(base=15, page_load=30, script=15)
        logger.info("浏览器已启动 (port=9333, user_data_path=./.chrome_profile)")
        return page
    except Exception as e:
        logger.error("浏览器初始化严重失败: %s", e)
        raise e


def ensure_login(page: ChromiumPage) -> bool:
    """检测登录状态，未登录则暂停 60 秒等人手工扫码"""
    page.get("https://www.zhipin.com/")
    time.sleep(4)
    for attempt in range(8):
        try:
            logged_in = page.run_js(
                'return document.querySelector(".user-nav") !== null || '
                'document.querySelector(".user-info") !== null || '
                'document.querySelector("[class*=user]") !== null'
            )
            if logged_in:
                logger.info("✅ 已登录")
                page.get("about:blank")
                time.sleep(0.5)
                return True
            is_verify = page.run_js(
                'return document.title.includes("安全") || document.title.includes("验证")'
            )
            if is_verify:
                logger.info("⏳ 等待安全验证通过... (%d/8)", attempt + 1)
        except Exception:
            pass
        time.sleep(3)

    logger.warning("🚨 检测到验证/登录拦截！浏览器已暂停运行 60 秒，请立即在弹出的窗口中手动处理！")
    time.sleep(60)
    page.get("https://www.zhipin.com/")
    time.sleep(4)
    try:
        logged_in = page.run_js(
            'return document.querySelector(".user-nav") !== null || '
            'document.querySelector(".user-info") !== null || '
            'document.querySelector("[class*=user]") !== null'
        )
        if logged_in:
            logger.info("✅ 登录成功")
            page.get("about:blank")
            time.sleep(0.5)
            return True
    except Exception:
        pass
    logger.warning("⚠ 登录检测超时，继续尝试抓取...")
    page.get("about:blank")
    time.sleep(0.5)
    return True


def _parse_joblist_response(body) -> list[dict]:
    """从 API 响应体中解析岗位列表，提取学历字段"""
    jobs = []
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return jobs
    if not isinstance(body, dict):
        return jobs
    zp_data = body.get("zpData") or body.get("data") or {}
    job_list = zp_data.get("jobList") if isinstance(zp_data, dict) else []
    if not job_list:
        job_list = body.get("data", {}).get("list", []) or body.get("list", [])
    for item in job_list:
        degree_name = item.get("degreeName", "") or item.get("education", "") or ""
        jobs.append({
            "jobName": item.get("jobName", ""),
            "brandName": item.get("brandName", ""),
            "salaryDesc": item.get("salaryDesc", ""),
            "cityName": item.get("cityName", ""),
            "encryptJobId": item.get("encryptJobId", ""),
            "securityId": item.get("securityId", ""),
            "degreeName": degree_name,
        })
    return jobs


def _smooth_scroll_to_bottom(page: ChromiumPage) -> None:
    """人类级平滑滚动到页面底部"""
    logger.info("开始平滑滚动到页面底部...")
    max_steps = 30
    for step in range(1, max_steps + 1):
        try:
            page.scroll.down(500)
        except Exception as e:
            logger.warning("滚动中途页面上下文丢失（第 %d 步）: %s", step, e)
            break
        pause = random.uniform(0.5, 1.2)
        time.sleep(pause)
        try:
            if page.scroll.is_at_bottom():
                logger.info("已滚动到页面底部（第 %d 步）", step)
                break
        except Exception:
            continue
    logger.info("平滑滚动完成")


def _collect_api_jobs(page: ChromiumPage, listen_timeout: int = 10) -> list[dict]:
    """在监听已启动的状态下，持续收集 API 响应包"""
    collected = []
    deadline = time.time() + listen_timeout
    while time.time() < deadline:
        try:
            remaining = max(0.5, deadline - time.time())
            r = page.listen.wait(timeout=remaining)
            if r and r.response and r.response.body:
                parsed = _parse_joblist_response(r.response.body)
                if parsed:
                    logger.info("API 拦截到 %d 条岗位", len(parsed))
                    collected.extend(parsed)
        except Exception:
            continue
    return collected


def extract_list_jobs(page: ChromiumPage) -> list[JobItem]:
    """
    访问搜索列表页，翻页抓取所有岗位。
    返回标准化 JobItem 列表。
    """
    logger.info("正在访问搜索列表页: %s", SEARCH_URL)

    seen = set()
    all_unique: list[JobItem] = []

    # 先启动监听，再导航
    page.listen.start("wapi/zpgeek/search/joblist.json")
    page.get(SEARCH_URL)
    logger.info("等待页面稳定（防刷新安全校验）...")
    time.sleep(3)

    def _is_education_allowed(j: dict) -> bool:
        """学历前置过滤：低于本科的直接丢弃"""
        degree = j.get("degreeName", "")
        if not degree:
            return True  # 无学历信息则放行（可能是 DOM 兜底数据）
        # 如果命中低学历关键词，丢弃
        if any(kw in degree for kw in LOW_EDUCATION_KEYWORDS):
            logger.info("⏭ 学历过滤丢弃 [%s]: %s - %s", degree, j.get("jobName", ""), j.get("brandName", ""))
            return False
        return True

    def _raw_to_jobitem(j: dict) -> JobItem:
        eid = j.get("encryptJobId", "")
        return JobItem(
            platform="BOSS直聘",
            job_name=j.get("jobName", ""),
            company=j.get("brandName", ""),
            salary=j.get("salaryDesc", ""),
            city=j.get("cityName", ""),
            url=f"https://www.zhipin.com/job_detail/{eid}.html",
            platform_job_id=eid,
        )

    # 第 1 页
    raw_jobs = _collect_api_jobs(page, listen_timeout=10)
    for j in raw_jobs:
        if not _is_education_allowed(j):
            continue
        eid = j.get("encryptJobId", "")
        if eid and eid not in seen:
            seen.add(eid)
            all_unique.append(_raw_to_jobitem(j))
    logger.info("第 1 页 API 获取到 %d 条岗位（累计 %d 条）", len(raw_jobs), len(all_unique))

    # 滚动触发懒加载
    logger.info("滚动触发懒加载，持续捕获 API 数据包...")
    _smooth_scroll_to_bottom(page)
    scroll_jobs = _collect_api_jobs(page, listen_timeout=5)
    for j in scroll_jobs:
        if not _is_education_allowed(j):
            continue
        eid = j.get("encryptJobId", "")
        if eid and eid not in seen:
            seen.add(eid)
            all_unique.append(_raw_to_jobitem(j))
    if scroll_jobs:
        logger.info("滚动后 API 新增 %d 条岗位（累计 %d 条）", len(scroll_jobs), len(all_unique))

    # 翻页循环
    page_num = 1
    while True:
        _smooth_scroll_to_bottom(page)
        try:
            next_btn = page.ele('.page-next', timeout=3)
            if not next_btn:
                next_btn = page.ele('.ui-icon-arrow-right', timeout=2)
            if not next_btn:
                logger.info("未找到下一页按钮，翻页结束")
                break
            cls = next_btn.attr('class') or ''
            if 'disabled' in cls:
                logger.info("下一页按钮已禁用，翻页结束")
                break
        except Exception:
            logger.info("未找到下一页按钮，翻页结束")
            break

        page_num += 1
        page_sleep = random.uniform(PAGE_SLEEP_MIN, PAGE_SLEEP_MAX)
        logger.info("准备翻到第 %d 页，等待 %.1f 秒...", page_num, page_sleep)
        time.sleep(page_sleep)

        try:
            next_btn.click()
            logger.info("已点击下一页 → 第 %d 页", page_num)
        except Exception as e:
            logger.warning("点击下一页失败: %s，翻页结束", e)
            break

        time.sleep(random.uniform(3, 5))
        page_jobs = _collect_api_jobs(page, listen_timeout=8)
        new_count = 0
        for j in page_jobs:
            if not _is_education_allowed(j):
                continue
            eid = j.get("encryptJobId", "")
            if eid and eid not in seen:
                seen.add(eid)
                all_unique.append(_raw_to_jobitem(j))
                new_count += 1
        logger.info("第 %d 页 API 获取到 %d 条新岗位（累计 %d 条）", page_num, new_count, len(all_unique))

    page.listen.stop()

    # DOM 兜底
    if not all_unique:
        logger.error("API 拦截完全失败，回退到 DOM 解析兜底...")
        try:
            page.wait.ele_displayed('.job-card-wrapper', timeout=15)
        except Exception:
            try:
                page.wait.ele_displayed('.job-list-box', timeout=8)
            except Exception:
                pass
        _smooth_scroll_to_bottom(page)
        try:
            dom_jobs = page.run_js("""
                const cards = document.querySelectorAll('.job-card-wrapper, .job-list-box > li, [class*=job-card], .geek-job-list > li');
                return Array.from(cards).map(card => {
                    const nameEl = card.querySelector('[class*=job-name]') || card.querySelector('.job-name') || card.querySelector('a[class*=job]');
                    const companyEl = card.querySelector('[class*=company-name]') || card.querySelector('.company-name');
                    const salaryEl = card.querySelector('[class*=salary]') || card.querySelector('.salary');
                    const areaEl = card.querySelector('[class*=job-area]') || card.querySelector('.job-area');
                    const linkEl = card.querySelector('a');
                    const href = linkEl?.getAttribute('href') || '';
                    return {
                        jobName: nameEl?.innerText?.trim() || nameEl?.textContent?.trim() || '',
                        brandName: companyEl?.innerText?.trim() || companyEl?.textContent?.trim() || '',
                        salaryDesc: salaryEl?.innerText?.trim() || salaryEl?.textContent?.trim() || '',
                        cityName: areaEl?.innerText?.trim() || areaEl?.textContent?.trim() || '',
                        encryptJobId: href.match(/job_detail\\/([^\\/]+)/)?.[1] || '',
                    };
                }).filter(j => j.jobName);
            """)
            if dom_jobs:
                for j in dom_jobs:
                    eid = j.get("encryptJobId", "")
                    if eid and eid not in seen:
                        seen.add(eid)
                        all_unique.append(JobItem(
                            platform="BOSS直聘",
                            job_name=j.get("jobName", ""),
                            company=j.get("brandName", ""),
                            salary=j.get("salaryDesc", ""),
                            city=j.get("cityName", ""),
                            url=f"https://www.zhipin.com/job_detail/{eid}.html",
                            platform_job_id=eid,
                        ))
                logger.info("DOM 兜底解析到 %d 条岗位", len(dom_jobs))
        except Exception as e:
            logger.warning("DOM 兜底解析失败: %s", e)

    if not all_unique:
        logger.error("所有方式均未获取到岗位数据，截取当前页面截图以供排查")
        try:
            screenshot_path = str(Path(__file__).parent / "error_page.png")
            page.get_screenshot(path=screenshot_path)
            logger.info("截图已保存: %s", screenshot_path)
        except Exception as e:
            logger.warning("截图保存失败: %s", e)

    logger.info("全部页面共获取到 %d 条岗位（去重后）", len(all_unique))
    return all_unique


def fetch_jd(page: ChromiumPage, job: JobItem) -> str:
    """
    访问岗位详情页，拦截 detail.json API 获取 postDescription。
    若 API 拦截失败，回退到 DOM 抓取。
    """
    detail_url = job.url
    jd = ""

    # 方式一：API 拦截
    try:
        page.listen.start("wapi/zpgeek/job/detail.json")
        page.get(detail_url)
        time.sleep(random.uniform(0.5, 1.5))
        r = page.listen.wait(timeout=DETAIL_TIMEOUT)
        if r and r.response and r.response.body:
            body = r.response.body
            if isinstance(body, str):
                body = json.loads(body)
            jd = body.get("zpData", {}).get("jobInfo", {}).get("postDescription", "")
            if jd:
                logger.info("✅ 通过 API 拦截获取到 JD（%d 字符）", len(jd))
        page.listen.stop()
    except Exception as e:
        logger.warning("API 拦截详情失败: %s", e)
        try:
            page.listen.stop()
        except Exception:
            pass

    # 方式二：DOM 回退
    if not jd:
        try:
            jd = page.run_js(
                "return document.querySelector('.job-sec-text')?.innerText || "
                "document.querySelector('.job-detail-section .text')?.innerText || ''"
            )
            if jd and len(jd) > 20:
                logger.info("✅ 通过 DOM 抓取获取到 JD（%d 字符）", len(jd))
            else:
                jd = ""
        except Exception as e:
            logger.warning("DOM 抓取详情失败: %s", e)

    return jd


def process_jobs(page: ChromiumPage, jobs: list[JobItem]) -> None:
    """
    循环处理每个岗位：获取 JD → AI 评估 → Notion 同步。
    双重防重复保险：本地缓存 + Notion URL 查重。
    """
    import ai_matcher
    import notion_sync

    processed_urls = _load_processed_urls()
    skipped_count = 0

    for idx, job in enumerate(jobs, 1):
        job_name = job.job_name
        company = job.company
        salary = job.salary
        city = job.city
        url = job.url

        if not job_name or not company:
            logger.warning("[%d/%d] 跳过无效岗位数据: %s", idx, len(jobs), job)
            continue

        # 本地缓存检查
        if url and url in processed_urls:
            logger.info("⏭ [%d/%d] 本地缓存命中，跳过: %s (%s)", idx, len(jobs), job_name, company)
            skipped_count += 1
            continue

        # 黑名单过滤
        if any(kw in job_name for kw in BLACKLIST_KEYWORDS):
            logger.info("⏭ [%d/%d] 命中黑名单，跳过实习/校招岗位: %s (%s)", idx, len(jobs), job_name, company)
            skipped_count += 1
            continue

        print(f"\n{'='*60}")
        print(f"[{idx}/{len(jobs)}] 处理岗位: {job_name} @ {company}")
        print(f"    薪资: {salary} | 地点: {city} | 平台: {job.platform}")
        print(f"    URL: {url}")
        print(f"{'='*60}")

        try:
            # 1. 获取 JD
            jd = fetch_jd(page, job) if url else ""

            # 2. AI 评估
            logger.info("调用 AI 评估: %s - %s", job_name, company)
            evaluation = ai_matcher.evaluate_job(
                title=job_name,
                company=company,
                salary=salary,
                location=city,
                platform=job.platform,
                jd_summary=jd,
            )
            score = evaluation.get("score", 0)
            summary = evaluation.get("summary", "")
            match_reasons = evaluation.get("match_reasons", [])
            mismatch_reasons = evaluation.get("mismatch_reasons", [])
            logger.info("AI 评估完成: 评分 %d/100 — %s", score, summary[:60] if summary else "无总结")

            # 3. Notion 同步
            logger.info("同步到 Notion: %s - %s", job_name, company)
            notion_sync.sync_job(
                title=job_name,
                company=company,
                platform=job.platform,
                url=url,
                location=city,
                salary_range=salary,
                jd_summary=jd[:2000] if jd else "",
                match_score=score,
                match_reasons=match_reasons,
                mismatch_reasons=mismatch_reasons,
                status="新发现",
                notes=summary,
            )

            # 4. 写入本地缓存
            if url:
                _mark_url_processed(url)
                processed_urls.add(url)

        except Exception as e:
            logger.error("处理岗位失败 [%s - %s]: %s", job_name, company, e, exc_info=True)
            # ── 旁路错题本 ──
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

        # 防反爬休眠
        if idx < len(jobs):
            sleep_time = random.uniform(SLEEP_MIN, SLEEP_MAX)
            logger.info("防反爬休眠 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)

    if skipped_count > 0:
        logger.info("本地缓存共跳过 %d 个已处理岗位", skipped_count)


# ── 全局浏览器引用 ──
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


def run() -> list[JobItem]:
    """
    运行 BOSS 直聘爬虫，返回标准化 JobItem 列表。
    可被 scheduler.py 或其他模块直接调用。
    """
    global _browser_page

    atexit.register(_cleanup_browser)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    page = None
    try:
        page = init_browser()
        _browser_page = page

        if not ensure_login(page):
            logger.warning("用户取消登录，退出")
            return []

        jobs = extract_list_jobs(page)
        if not jobs:
            logger.warning("未从列表页获取到任何岗位，退出")
            return []

        print(f"\n📋 共获取到 {len(jobs)} 个岗位，开始逐个处理...")
        process_jobs(page, jobs)

        print(f"\n{'='*60}")
        print("✅ BOSS直聘抓取完成！")
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
    """独立运行入口"""
    run()


if __name__ == "__main__":
    main()
