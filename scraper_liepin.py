#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
scraper_liepin.py — DrissionPage 版猎聘网爬虫

适配器模式：基于 scraper_drission.py 改造，仅替换：
  1. SEARCH_URL → 猎聘搜索链接
  2. DOM 解析规则 → 猎聘页面结构
  3. 详情页 JD 抓取 → 猎聘详情页结构

保持以下逻辑完全不变：
  - ai_matcher 调用（AI 评估）
  - notion_sync 调用（Notion 写入）
  - 双重防重复写入（本地缓存 + Notion URL 查重）

用法:
  python scraper_liepin.py
"""

import sys
import io
import json
import random
import time
import logging
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
logger = logging.getLogger("scraper_liepin")

# ==========================================
# 🎯 抓取条件控制面板 (猎聘网参数字典)
# ==========================================
KEYWORD = "AI产品经理"

# 城市代码 (city)
# 常见城市: 北京(110000), 上海(310000), 广州(440100), 深圳(440300), 杭州(330100), 全国(0)
CITY_CODE = "0"

# 工作经验 (experience)
# 常见经验: 不限(空), 1年以下(1), 1-3年(2), 3-5年(3), 5-10年(4), 10年以上(5)
EXPERIENCE = ""

# 学历要求 (degree)
# 常见学历: 不限(空), 大专(20), 本科(30), 硕士(40), 博士(50)
DEGREE = "30"  # 默认设置为 本科

# 拼接最终的搜索 URL
SEARCH_URL = f"https://www.liepin.com/zhaopin/?key={KEYWORD}&dq={CITY_CODE}&pubTime=&currentPage=0&pageSize=40&scene=history&key={KEYWORD}"

LISTEN_TIMEOUT = 8       # 搜索列表监听超时（秒）
DETAIL_TIMEOUT = 5       # 详情页监听超时（秒）
MAX_JOBS = 3             # 本次处理前 3 个岗位
SLEEP_MIN, SLEEP_MAX = 3, 6  # 防反爬休眠范围

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
    """初始化 ChromiumPage，配置 user_data_path 和端口"""
    co = ChromiumOptions()
    co.set_user_data_path("./.chrome_profile")
    # 反检测
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--lang=zh-CN")
    co.set_pref("excludeSwitches", ["enable-automation"])
    co.set_pref("useAutomationExtension", False)
    # 随机窗口尺寸
    w = random.choice([1440, 1512, 1680, 1920]) + random.randint(-20, 20)
    h = random.choice([900, 1080, 1050]) + random.randint(-20, 20)
    co.set_argument(f"--window-size={w},{h}")

    page = ChromiumPage(addr_or_opts=co)
    page.set.timeouts(base=30, page_load=30, script=20)

    # 注入反检测 JS
    try:
        page.run_js("""
            Object.defineProperty(navigator, "webdriver", {get: () => undefined});
            Object.defineProperty(navigator, "plugins", {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, "languages", {get: () => ["zh-CN","zh","en"]});
            window.chrome = {runtime: {}, loadTimes: () => ({}), csi: () => ({})};
        """)
    except Exception:
        pass

    logger.info("浏览器已启动 (user_data_path=./.chrome_profile)")
    return page


def ensure_login(page: ChromiumPage) -> bool:
    """
    检测登录状态，未登录则弹窗提示用户手动登录。
    使用猎聘网首页检测登录。
    返回 True 表示已登录，False 表示用户取消。
    """
    page.get("https://www.liepin.com/")
    time.sleep(4)

    for attempt in range(8):
        try:
            # 检测页面是否包含登录后元素
            logged_in = page.run_js(
                'return document.querySelector(".user-name") !== null || '
                'document.querySelector("[class*=header-quick-menu]") !== null || '
                'document.querySelector(".header-login-btn") === null'
            )
            if logged_in:
                logger.info("✅ 已登录")
                page.get("about:blank")
                time.sleep(0.5)
                return True
        except Exception:
            pass
        time.sleep(3)

    # 未登录 — 提示用户手动登录
    print("\n" + "=" * 60)
    print("🔑 需要登录猎聘网")
    print("请在已打开的 Chrome 浏览器中手动登录猎聘网")
    print("登录成功后，按 Enter 键继续...")
    print("=" * 60)
    input()

    # 再次检测
    page.get("https://www.liepin.com/")
    time.sleep(4)
    try:
        logged_in = page.run_js(
            'return document.querySelector(".user-name") !== null || '
            'document.querySelector("[class*=header-quick-menu]") !== null'
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


def extract_list_jobs(page: ChromiumPage) -> list[dict]:
    """
    访问猎聘搜索列表页，通过 DOM 解析获取岗位列表。

    猎聘网页面为 SSR 渲染，岗位数据直接在 DOM 中，
    因此采用 DOM 解析方式提取。

    返回列表，每项包含 jobName, brandName, salaryDesc, cityName, encryptJobId 等。
    """
    logger.info("正在访问猎聘搜索列表页: %s", SEARCH_URL)

    page.get(SEARCH_URL)
    time.sleep(4)

    # 滚动页面触发懒加载
    try:
        page.run_js("window.scrollTo(0, 600)")
        time.sleep(1)
    except Exception:
        pass

    # 从 DOM 中提取岗位列表
    all_jobs = []
    try:
        jobs_data = page.run_js("""
            const cards = document.querySelectorAll('.job-card-pc-container');
            return Array.from(cards).slice(0, 10).map(card => {
                // 获取卡片中所有文本
                const allText = card.innerText || '';

                // 岗位名称：第一个带 title 的 ellipsis-1
                const titleEl = card.querySelector('.ellipsis-1[title]');
                const jobName = titleEl?.title || titleEl?.innerText?.trim() || '';

                // 公司名称：在卡片文本中，位于薪资/经验等信息之后
                // 从卡片文本中提取：去掉已知字段后，剩下的非空行中找公司名
                const lines = allText.split('\\n').map(l => l.trim()).filter(l => l);
                let brandName = '';
                // 公司名通常在薪资行之后、招聘者信息之前
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    // 跳过已知字段行
                    if (/[kK]/.test(line) && /\\d/.test(line)) continue;  // 薪资
                    if (line.includes('【') || line.includes('】')) continue;  // 地点
                    if (/急聘|应届|经验|学历|大专|本科|硕士|博士|统招/.test(line)) continue;
                    if (/HR|在线|广告/.test(line)) continue;
                    if (line.length > 2 && line.length < 50) {
                        brandName = line;
                        break;
                    }
                }

                // 薪资：包含 k 或 K 的数字文本
                const salaryEl = card.querySelector('[class*="E8PWS"], [class*="salary"]');
                let salaryDesc = salaryEl?.innerText?.trim() || '';
                if (!salaryDesc) {
                    // 回退：从文本中找薪资模式
                    const match = allText.match(/(\\d+[kK]-\\d+[kK]|\\d+[kK]|薪资面议)/);
                    salaryDesc = match ? match[1] : '';
                }

                // 地点：在 【】 中的内容
                const cityMatch = allText.match(/【(.+?)】/);
                let cityName = cityMatch ? cityMatch[1].trim() : '';

                // 详情链接 - 提取 jobId
                const linkEl = card.querySelector('a[data-nick="job-detail-job-info"]');
                const href = linkEl?.getAttribute('href') || '';
                const jobIdMatch = href.match(/\\/job\\/(\\d+)\\.shtml/);
                const encryptJobId = jobIdMatch ? jobIdMatch[1] : '';

                return { jobName, brandName, salaryDesc, cityName, encryptJobId };
            });
        """)
        if jobs_data:
            for item in jobs_data:
                if item.get("jobName") and item.get("brandName"):
                    all_jobs.append(item)
            logger.info("DOM 解析到 %d 条岗位", len(all_jobs))
        else:
            logger.warning("DOM 解析返回空")
    except Exception as e:
        logger.warning("DOM 解析失败: %s", e)

    # 去重（按 encryptJobId）
    seen = set()
    unique_jobs = []
    for job in all_jobs:
        eid = job.get("encryptJobId", "")
        if eid and eid not in seen:
            seen.add(eid)
            unique_jobs.append(job)

    logger.info("列表页共获取到 %d 条岗位（去重后 %d 条）", len(all_jobs), len(unique_jobs))
    return unique_jobs[:MAX_JOBS]


def fetch_jd(page: ChromiumPage, encrypt_job_id: str) -> str:
    """
    访问猎聘岗位详情页，通过 DOM 抓取职位介绍（JD）。
    猎聘详情页为 SSR 渲染，JD 在页面 DOM 中。
    """
    detail_url = f"https://www.liepin.com/job/{encrypt_job_id}.shtml"
    jd = ""

    try:
        page.get(detail_url)
        time.sleep(random.uniform(2, 4))

        # DOM 抓取 JD
        jd = page.run_js("""
            // 尝试多种选择器获取职位介绍
            const selectors = [
                '.job-description',
                '.job-detail',
                '[class*=job-description]',
                '[class*=job-detail]',
                '.job-require',
                '.resume',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText && el.innerText.length > 50) {
                    return el.innerText.trim();
                }
            }
            // 回退：获取整个页面中"职位介绍"之后的内容
            const body = document.body?.innerText || '';
            const idx = body.indexOf('职位介绍');
            if (idx > 0) {
                return body.substring(idx + 5, idx + 3000).trim();
            }
            return '';
        """)
        if jd and len(jd) > 20:
            logger.info("✅ 通过 DOM 抓取获取到 JD（%d 字符）", len(jd))
        else:
            jd = ""
            logger.warning("DOM 抓取 JD 内容过短或为空")
    except Exception as e:
        logger.warning("抓取详情页失败: %s", e)

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
        url = f"https://www.liepin.com/job/{encrypt_id}.shtml" if encrypt_id else ""

        if not job_name or not company:
            logger.warning("[%d/%d] 跳过无效岗位数据: %s", idx, MAX_JOBS, job)
            continue

        # ── 本地缓存检查（第一重防重复） ──
        if url and url in processed_urls:
            logger.info("⏭ [%d/%d] 本地缓存命中，跳过: %s (%s)", idx, MAX_JOBS, job_name, company)
            skipped_count += 1
            continue

        print(f"\n{'='*60}")
        print(f"[{idx}/{MAX_JOBS}] 处理岗位: {job_name} @ {company}")
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
                platform="猎聘网",
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
                platform="猎聘网",
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

        # 防反爬休眠（最后一个岗位不休息）
        if idx < len(jobs):
            sleep_time = random.uniform(SLEEP_MIN, SLEEP_MAX)
            logger.info("防反爬休眠 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)

    if skipped_count > 0:
        logger.info("本地缓存共跳过 %d 个已处理岗位", skipped_count)


def main():
    """主流程"""
    page = None
    try:
        page = init_browser()

        # 0. 确保已登录
        if not ensure_login(page):
            logger.warning("用户取消登录，退出")
            return

        # 1. 解析搜索列表，获取前 3 个岗位
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

    except Exception as e:
        logger.error("脚本运行失败: %s", e, exc_info=True)
    finally:
        if page:
            try:
                page.quit()
                logger.info("浏览器已关闭")
            except Exception:
                pass


if __name__ == "__main__":
    main()
