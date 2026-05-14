#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
crawler_tencent.py — 腾讯招聘精准爬虫

使用 requests 直接调用腾讯招聘 API，只抓取「AI产品经理」相关岗位。

腾讯招聘 API:
  搜索: GET https://careers.tencent.com/tencentcareer/api/post/Query
  详情: GET https://careers.tencent.com/tencentcareer/api/post/ByPostId

用法:
  conda run -n job_env python crawler_tencent.py
"""

import sys, io, json, time, logging, re, random, subprocess, os
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests

# ── UTF-8 输出 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tencent_crawler")

# ── 配置 ──
SEARCH_API = "https://careers.tencent.com/tencentcareer/api/post/Query"
DETAIL_API = "https://careers.tencent.com/tencentcareer/api/post/ByPostId"
COMPANY_NAME = "腾讯 (Tencent)"
MAX_PAGES = 5
MIN_WAIT = 0.5
MAX_WAIT = 1.0
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "openclaw_jobs.json"
BRIDGE_SCRIPT = Path(__file__).parent / "openclaw_bridge.py"

# ── 请求头 ──
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://careers.tencent.com/search.html",
    "Origin": "https://careers.tencent.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

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


def search_jobs(page_index: int = 1, page_size: int = 10) -> Optional[dict]:
    """调用腾讯招聘搜索 API"""
    params = {
        "timestamp": int(time.time() * 1000),
        "countryId": "",
        "cityId": "",
        "bgIds": "",
        "productId": "",
        "categoryId": "",
        "parentCategoryId": "",
        "attrId": "1",
        "keyword": "AI产品经理",
        "pageIndex": page_index,
        "pageSize": page_size,
        "language": "zh-cn",
        "area": "us",
    }

    try:
        resp = requests.get(SEARCH_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Code") == 200 and data.get("Data"):
            return data["Data"]
        else:
            logger.warning(f"⚠️ API 返回异常: {data.get('Message', '未知错误')}")
            return None
    except Exception as e:
        logger.error(f"❌ 搜索 API 请求失败: {e}")
        return None


def get_job_detail(post_id: str) -> Optional[dict]:
    """调用腾讯招聘详情 API"""
    params = {
        "timestamp": int(time.time() * 1000),
        "postId": post_id,
        "language": "zh-cn",
    }

    try:
        resp = requests.get(DETAIL_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Code") == 200 and data.get("Data"):
            return data["Data"]
        else:
            logger.warning(f"⚠️ 详情 API 返回异常: {data.get('Message', '未知错误')}")
            return None
    except Exception as e:
        logger.error(f"❌ 详情 API 请求失败: {e}")
        return None


def crawl() -> list[dict]:
    """主抓取流程"""
    all_jobs = []

    logger.info(f"🌐 开始抓取腾讯招聘 (API 模式)")
    logger.info(f"   搜索 API: {SEARCH_API}")
    logger.info(f"   详情 API: {DETAIL_API}")
    logger.info(f"   关键词: AI产品经理")
    logger.info(f"   最大翻页: {MAX_PAGES} 页")

    for page_num in range(1, MAX_PAGES + 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"📄 第 {page_num}/{MAX_PAGES} 页")
        logger.info(f"{'='*60}")

        data = search_jobs(page_index=page_num, page_size=10)
        if not data:
            logger.warning("⚠️ 搜索 API 返回空数据，停止翻页")
            break

        posts = data.get("Posts", [])
        logger.info(f"📊 当前页 {len(posts)} 条")

        if not posts:
            logger.info("🏁 当前页无数据，已到达最后一页")
            break

        for post in posts:
            title = post.get("RecruitPostName", "").strip()
            post_id = post.get("PostId", "")
            url = f"https://careers.tencent.com/jobdesc.html?postId={post_id}"

            if not title or not post_id:
                continue

            # 产品经理关键词过滤
            if not is_pm_related(title):
                logger.info(f"  🚫 过滤非产品岗: {title}")
                continue

            logger.info(f"  📌 {title} -> {url}")

            # 获取详情
            random_sleep("请求详情前等待")
            detail = get_job_detail(post_id)

            if detail:
                # 提取 JD 内容
                responsibility = detail.get("Responsibility", "") or ""
                requirement = detail.get("Requirement", "") or ""

                # 清理空白
                responsibility = re.sub(r"[ \t]+", " ", responsibility).strip()
                requirement = re.sub(r"[ \t]+", " ", requirement).strip()

                # 提取地点
                location = detail.get("LocationName", "") or "深圳"

                job_data = {
                    "title": title,
                    "company": COMPANY_NAME,
                    "salary": "面议",
                    "location": location,
                    "url": url,
                    "job_description": responsibility,
                    "job_requirements": requirement,
                    "full_jd": responsibility,
                    "requirements": requirement,
                }
                all_jobs.append(job_data)
                logger.info(f"  ✅ 提取完成: 描述 {len(responsibility)} 字符, 要求 {len(requirement)} 字符")
            else:
                # 降级：使用搜索 API 返回的 Responsibility
                responsibility = post.get("Responsibility", "") or ""
                requirement = post.get("Requirement", "") or ""
                location = post.get("LocationName", "") or "深圳"

                if responsibility:
                    job_data = {
                        "title": title,
                        "company": COMPANY_NAME,
                        "salary": "面议",
                        "location": location,
                        "url": url,
                        "job_description": responsibility,
                        "job_requirements": requirement,
                        "full_jd": responsibility,
                        "requirements": requirement,
                    }
                    all_jobs.append(job_data)
                    logger.info(f"  ⚠️ 详情 API 失败，使用搜索数据: 描述 {len(responsibility)} 字符")
                else:
                    logger.warning(f"  ❌ 提取失败: {title}")

    logger.info(f"\n{'='*60}")
    logger.info(f"🎉 腾讯招聘抓取完成！共 {len(all_jobs)} 条")
    logger.info(f"{'='*60}")

    return all_jobs


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
    print("📊 腾讯招聘精准爬取结果")
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
    print("🔍 腾讯 (Tencent) 精准爬虫 (API 模式)")
    print("=" * 70)
    print(f"  搜索 API: {SEARCH_API}")
    print(f"  详情 API: {DETAIL_API}")
    print(f"  关键词: AI产品经理")
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
