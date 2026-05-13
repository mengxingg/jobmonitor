#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
scraper_drission.py — DrissionPage 版 BOSS直聘爬虫

基于 boss_dp.py 的调研成果，使用 DrissionPage 的 Listener 模式：
  1. 拦截搜索列表 API (wapi/zpgeek/search/joblist.json) 获取前 3 个岗位
  2. 逐个访问详情页，拦截详情 API (wapi/zpgeek/job/detail.json) 获取 JD
  3. 调用 ai_matcher 评估 + notion_sync 同步

用法:
  python scraper_drission.py
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

# 确保 stdout 为 UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from DrissionPage import ChromiumPage, ChromiumOptions

import ai_matcher
import notion_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scraper_drission")

# ==========================================
# 🎯 抓取条件控制面板 (BOSS直聘参数字典)
# ==========================================
KEYWORD = "AI产品经理"

# 城市代码 (city)
# 常见城市: 北京(101010100), 上海(101020100), 广州(101280100), 深圳(101280600), 杭州(101210100), 全国(100010000)
CITY_CODE = "100010000"

# 工作经验 (experience)
# 常见经验: 不限(留空), 1-3年(104), 3-5年(105), 5-10年(106), 10年以上(107)
EXPERIENCE = ""

# 学历要求 (degree)
# 常见学历: 不限(留空), 大专(202), 本科(203), 硕士(204), 博士(205)
DEGREE = ""  # 学历不限（移除 degree=203 限制，获取全量岗位）

# 拼接最终的搜索 URL
SEARCH_URL = f"https://www.zhipin.com/web/geek/job?query={KEYWORD}&city={CITY_CODE}&experience={EXPERIENCE}&degree={DEGREE}"
LISTEN_TIMEOUT = 8       # 搜索列表监听超时（秒）
DETAIL_TIMEOUT = 3       # 详情页监听超时（秒）
MAX_PAGES = 3            # 翻页抓取页数（第 1 页 + 额外 2 页）
SLEEP_MIN, SLEEP_MAX = 3, 6  # 防反爬休眠范围
PAGE_SLEEP_MIN, PAGE_SLEEP_MAX = 5, 10  # 翻页间隔休眠范围

# ── 黑名单关键词（过滤实习/校招岗位，节省 API 成本） ──
BLACKLIST_KEYWORDS = ['实习', '校招', '应届', '26届', '27届']

# ── 本地缓存文件（双重防重复保险） ──
PROCESSED_JOBS_FILE = Path(__file__).parent / "processed_jobs.txt"


def _load_processed_urls() -> set[str]:
    """
    从 processed_jobs.txt 加载已处理过的 URL 集合。
    每行一个 URL，支持 # 注释和空行。
    """
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
    """将 URL 追加写入 processed_jobs.txt"""
    with open(PROCESSED_JOBS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")
    logger.debug("已写入本地缓存: %s", url)


def init_browser() -> ChromiumPage:
    """
    初始化 ChromiumPage，配置自动端口 + 无头模式 + 防卡死参数。
    
    核心策略：
      1. co.auto_port() — 强制使用自动端口，彻底避开 9222 冲突
      2. co.headless(True) — 无头模式，不弹界面，防止卡顿
      3. co.set_user_data_path('./.chrome_profile') — 保持用户登录态
      4. 防卡死参数矩阵 — 屏蔽所有干扰弹窗
    """
    co = ChromiumOptions()

    # 1. 核心：强制指定独立端口 9333，彻底避开 9222 冲突
    co.set_local_port(9333)

    # 2. 核心：保持用户登录态
    co.set_user_data_path('./.chrome_profile')

    # 3. 有界面模式（Boss 直聘对无头模式风控极严，必须退回到有界面）
    co.headless(False)

    # 4. 防卡死参数矩阵
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--disable-popup-blocking')
    co.set_argument('--disable-infobars')
    co.set_argument('--hide-crash-restore-bubble')

    # 反检测
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--lang=zh-CN")
    co.set_pref("excludeSwitches", ["enable-automation"])
    co.set_pref("useAutomationExtension", False)

    try:
        page = ChromiumPage(addr_or_opts=co)
        # 设置全局超时时间
        page.set.timeouts(base=15, page_load=30, script=15)
        logger.info("浏览器已启动 (auto_port + headless, user_data_path=./.chrome_profile)")
        return page
    except Exception as e:
        logger.error("浏览器初始化严重失败: %s", e)
        # 如果连自动端口都失败了，说明环境彻底卡死，直接抛出异常
        raise e


def ensure_login(page: ChromiumPage) -> bool:
    """
    检测登录状态，未登录则弹窗提示用户手动登录。
    使用 BOSS直聘首页检测登录，避免提前加载搜索列表 API。
    检测完成后导航到 about:blank，确保 extract_list_jobs 首次访问搜索页时
    能完整捕获 joblist.json API 请求。
    返回 True 表示已登录，False 表示用户取消。
    """
    page.get("https://www.zhipin.com/")
    time.sleep(4)

    for attempt in range(8):
        try:
            # 检测页面是否包含登录后元素（如用户名、头像等）
            logged_in = page.run_js(
                'return document.querySelector(".user-nav") !== null || '
                'document.querySelector(".user-info") !== null || '
                'document.querySelector("[class*=user]") !== null'
            )
            if logged_in:
                logger.info("✅ 已登录")
                # 导航到空白页，清空当前页面状态
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

    # 未登录 — 暂停 60 秒，让用户在弹出的浏览器界面上手动扫码
    logger.warning("🚨 检测到验证/登录拦截！浏览器已暂停运行 60 秒，请立即在弹出的窗口中手动处理！")
    time.sleep(60)

    # 再次检测
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
    return True  # 仍然继续，可能页面加载慢


def _parse_joblist_response(body) -> list[dict]:
    """从 API 响应体中解析岗位列表（通用格式）"""
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
        jobs.append({
            "jobName": item.get("jobName", ""),
            "brandName": item.get("brandName", ""),
            "salaryDesc": item.get("salaryDesc", ""),
            "cityName": item.get("cityName", ""),
            "encryptJobId": item.get("encryptJobId", ""),
            "securityId": item.get("securityId", ""),
        })
    return jobs


def _smooth_scroll_to_bottom(page: ChromiumPage) -> None:
    """
    『人类级』平滑滚动到页面底部。
    使用 DrissionPage 原生 page.scroll.down() API，避免 run_js 注入风险。
    每次滚动 500 像素，间隔随机 0.5~1.2 秒，模拟真人浏览行为。
    如果滚动过程中页面上下文丢失（风控强制刷新），安全退出不崩溃。
    """
    logger.info("开始平滑滚动到页面底部...")
    max_steps = 30  # 安全上限，防止死循环
    for step in range(1, max_steps + 1):
        try:
            # 使用 DrissionPage 原生滚动 API，比 run_js 更底层、更稳定
            page.scroll.down(500)
        except Exception as e:
            logger.warning("滚动中途页面上下文丢失或超时（第 %d 步）: %s，提前结束滚动", step, e)
            break

        # 随机停顿 0.5~1.2 秒，模拟真人
        pause = random.uniform(0.5, 1.2)
        time.sleep(pause)

        # 检查是否已到底部（通过判断页面高度是否变化）
        try:
            if page.scroll.is_at_bottom():
                logger.info("已滚动到页面底部（第 %d 步）", step)
                break
        except Exception:
            # 如果检查底部也失败，继续尝试滚动
            logger.debug("检查滚动位置失败（第 %d 步），继续...", step)
            continue

    logger.info("平滑滚动完成")


def _collect_api_jobs(page: ChromiumPage, listen_timeout: int = 10) -> list[dict]:
    """
    在监听已启动的状态下，收集当前及后续的 API 响应包。
    返回解析出的岗位列表（合并所有包）。
    """
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


def extract_list_jobs(page: ChromiumPage) -> list[dict]:
    """
    访问搜索列表页，翻页抓取所有岗位。

    核心策略（API 拦截优先，彻底绕过字体加密）：
      1. 先启动监听，再导航到搜索页，确保首次加载的 API 请求也能被捕获
      2. 导航后强制等待 3 秒，让 Boss 直聘的安全校验刷新完成
      3. 收集第 1 页的 API 数据包
      4. 平滑滚动到底部触发懒加载，滚动过程中持续捕获新 API 包
      5. 循环翻页：滚动到底部 → 点击下一页 → 收集 API 包，直到没有下一页
      6. 翻页间隔随机休眠 5~10 秒防封禁
      7. 全部失败则截图保存现场

    返回列表，每项包含 jobName, brandName, salaryDesc, cityName, encryptJobId 等。
    """
    logger.info("正在访问搜索列表页: %s", SEARCH_URL)

    seen = set()       # 全局去重集合
    all_unique = []    # 全部页累计结果

    # ── 第 1 页：先启动监听，再导航（确保首次 API 请求也能被捕获） ──
    page.listen.start("wapi/zpgeek/search/joblist.json")
    page.get(SEARCH_URL)
    logger.info("等待页面稳定（防刷新安全校验）...")
    time.sleep(3)

    # 收集第 1 页的 API 数据包
    page_jobs = _collect_api_jobs(page, listen_timeout=10)
    for job in page_jobs:
        eid = job.get("encryptJobId", "")
        if eid and eid not in seen:
            seen.add(eid)
            all_unique.append(job)
    logger.info("第 1 页 API 获取到 %d 条岗位（累计 %d 条）", len(page_jobs), len(all_unique))

    # 平滑滚动到底部触发懒加载，滚动过程中持续捕获新 API 包
    logger.info("滚动触发懒加载，持续捕获 API 数据包...")
    _smooth_scroll_to_bottom(page)
    scroll_jobs = _collect_api_jobs(page, listen_timeout=5)
    for job in scroll_jobs:
        eid = job.get("encryptJobId", "")
        if eid and eid not in seen:
            seen.add(eid)
            all_unique.append(job)
    if scroll_jobs:
        logger.info("滚动后 API 新增 %d 条岗位（累计 %d 条）", len(scroll_jobs), len(all_unique))

    # ── 翻页循环：第 2 页起，直到没有下一页 ──
    page_num = 1
    while True:
        # 先平滑滚动到底部，确保分页按钮可见
        _smooth_scroll_to_bottom(page)

        # 检查是否有下一页按钮
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

        # 防反爬休眠（随机 5~10 秒）
        page_sleep = random.uniform(PAGE_SLEEP_MIN, PAGE_SLEEP_MAX)
        logger.info("准备翻到第 %d 页，等待 %.1f 秒...", page_num, page_sleep)
        time.sleep(page_sleep)

        # 点击下一页（监听已在运行中，无需重启）
        try:
            next_btn.click()
            logger.info("已点击下一页 → 第 %d 页", page_num)
        except Exception as e:
            logger.warning("点击下一页失败: %s，翻页结束", e)
            break

        # 等待新页面加载稳定（3~5 秒随机）
        page_load_sleep = random.uniform(3, 5)
        time.sleep(page_load_sleep)

        # 收集当前页的 API 数据包
        page_jobs = _collect_api_jobs(page, listen_timeout=8)
        new_count = 0
        for job in page_jobs:
            eid = job.get("encryptJobId", "")
            if eid and eid not in seen:
                seen.add(eid)
                all_unique.append(job)
                new_count += 1
        logger.info("第 %d 页 API 获取到 %d 条新岗位（累计 %d 条）", page_num, new_count, len(all_unique))

    page.listen.stop()

    # ── 兜底：如果 API 一条都没抓到，回退到 DOM 解析 ──
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
                for job in dom_jobs:
                    eid = job.get("encryptJobId", "")
                    if eid and eid not in seen:
                        seen.add(eid)
                        all_unique.append(job)
                logger.info("DOM 兜底解析到 %d 条岗位", len(dom_jobs))
        except Exception as e:
            logger.warning("DOM 兜底解析失败: %s", e)

    # ── 降级容错 ──
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


def fetch_jd(page: ChromiumPage, encrypt_job_id: str) -> str:
    """
    访问岗位详情页，拦截 detail.json API 获取 postDescription。
    若 API 拦截失败，回退到 DOM 抓取。
    """
    detail_url = f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
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


def process_jobs(page: ChromiumPage, jobs: list[dict]) -> None:
    """
    循环处理每个岗位：获取 JD → AI 评估 → Notion 同步。
    
    双重防重复保险：
      1. 本地 processed_jobs.txt 缓存（跳过已处理的 URL）
      2. Notion 端按 URL 查重（由 notion_sync.find_existing_by_url 实现）
    """
    # 加载本地缓存
    processed_urls = _load_processed_urls()
    skipped_count = 0

    for idx, job in enumerate(jobs, 1):
        job_name = job.get("jobName", "")
        company = job.get("brandName", "")
        salary = job.get("salaryDesc", "")
        city = job.get("cityName", "")
        encrypt_id = job.get("encryptJobId", "")
        url = f"https://www.zhipin.com/job_detail/{encrypt_id}.html" if encrypt_id else ""

        if not job_name or not company:
            logger.warning("[%d/%d] 跳过无效岗位数据: %s", idx, len(jobs), job)
            continue

        # ── 本地缓存检查（第一重防重复） ──
        if url and url in processed_urls:
            logger.info("⏭ [%d/%d] 本地缓存命中，跳过: %s (%s)", idx, len(jobs), job_name, company)
            skipped_count += 1
            continue

        # ── 黑名单过滤（实习/校招岗位，节省 API 成本） ──
        if any(kw in job_name for kw in BLACKLIST_KEYWORDS):
            logger.info("⏭ [%d/%d] 命中黑名单，跳过实习/校招岗位: %s (%s)", idx, len(jobs), job_name, company)
            skipped_count += 1
            continue

        print(f"\n{'='*60}")
        print(f"[{idx}/{len(jobs)}] 处理岗位: {job_name} @ {company}")
        print(f"    薪资: {salary} | 地点: {city}")
        print(f"    URL: {url}")
        print(f"{'='*60}")

        try:
            # 1. 获取 JD
            jd = fetch_jd(page, encrypt_id) if encrypt_id else ""

            # 2. AI 评估
            logger.info("调用 AI 评估: %s - %s", job_name, company)
            evaluation = ai_matcher.evaluate_job(
                title=job_name,
                company=company,
                salary=salary,
                location=city,
                platform="BOSS直聘",
                jd_summary=jd,
            )
            score = evaluation.get("score", 0)
            summary = evaluation.get("summary", "")
            match_reasons = evaluation.get("match_reasons", [])
            mismatch_reasons = evaluation.get("mismatch_reasons", [])
            logger.info("AI 评估完成: 评分 %d/100 — %s", score, summary[:60] if summary else "无总结")

            # 3. Notion 同步（内部含第二重查重：按 URL 查询 Notion 数据库）
            logger.info("同步到 Notion: %s - %s", job_name, company)
            notion_sync.sync_job(
                title=job_name,
                company=company,
                platform="BOSS直聘",
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

            # 4. 同步成功后写入本地缓存
            if url:
                _mark_url_processed(url)
                processed_urls.add(url)

        except Exception as e:
            logger.error("处理岗位失败 [%s - %s]: %s", job_name, company, e, exc_info=True)
            # ── 旁路错题本：写入 failed_jobs_inbox.md（人工兜底用） ──
            try:
                inbox_path = Path(__file__).parent / "failed_jobs_inbox.md"
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                safe_name = job_name or "未知"
                safe_company = company or "未知"
                safe_url = url or "未知"
                brief = str(e).split("\n")[0][:80]
                line = f"- [ ] **[{timestamp}]** 岗位：{safe_name} (公司：{safe_company}) - [查看岗位]({safe_url}) - 报错: {brief}\n"
                with open(inbox_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as write_err:
                logger.warning("写入错题本失败: %s", write_err)

        # 防反爬休眠（最后一个岗位不休息）
        if idx < len(jobs):
            sleep_time = random.uniform(SLEEP_MIN, SLEEP_MAX)
            logger.info("防反爬休眠 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)

    if skipped_count > 0:
        logger.info("本地缓存共跳过 %d 个已处理岗位", skipped_count)


# ── 全局浏览器引用（用于 atexit + 信号处理强制释放） ──
_browser_page: ChromiumPage | None = None


def _cleanup_browser():
    """强制释放浏览器资源，关闭 9222 端口。被 atexit 和信号处理器调用。"""
    global _browser_page
    if _browser_page is not None:
        try:
            _browser_page.quit()
            logger.info("✅ 浏览器资源已释放 (9222 端口已关闭)")
        except Exception:
            pass
        finally:
            _browser_page = None


def _signal_handler(signum, frame):
    """信号处理器：捕获 SIGINT/SIGTERM 后强制清理浏览器"""
    logger.warning("接收到信号 %d，正在强制释放浏览器资源...", signum)
    _cleanup_browser()
    sys.exit(128 + signum)


def main():
    """主流程"""
    global _browser_page

    # 注册 atexit 和信号处理器，确保 100% 释放资源
    atexit.register(_cleanup_browser)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    page = None
    try:
        page = init_browser()
        _browser_page = page  # 赋值给全局引用

        # 0. 确保已登录
        if not ensure_login(page):
            logger.warning("用户取消登录，退出")
            return

        # 1. 拦截搜索列表，获取所有岗位（无数量限制）
        jobs = extract_list_jobs(page)
        if not jobs:
            logger.warning("未从列表页获取到任何岗位，退出")
            return

        print(f"\n📋 共获取到 {len(jobs)} 个岗位，开始逐个处理...")

        # 2. 循环处理每个岗位
        process_jobs(page, jobs)

        print(f"\n{'='*60}")
        print("✅ 全部处理完成！")
        print(f"{'='*60}")

    except KeyboardInterrupt:
        logger.warning("用户手动中断，正在清理...")
    except Exception as e:
        logger.error("脚本运行失败: %s", e, exc_info=True)
    finally:
        _cleanup_browser()


if __name__ == "__main__":
    main()
