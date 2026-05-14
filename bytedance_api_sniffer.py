#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
bytedance_api_sniffer.py — 字节跳动招聘 API 嗅探器 v5.0

核心策略：
  1. 访问 jobs.bytedance.com 首页（SPA 入口）
  2. 拦截所有 XHR/Fetch 请求
  3. 通过 JS 注入，在页面上下文中调用内部 API（利用页面 cookies/token）
  4. 尝试多种可能的 API 路径
  5. 解析响应提取岗位数据

用法:
  python bytedance_api_sniffer.py
"""

import sys, io, json, time, logging, re
from pathlib import Path
from datetime import datetime

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from DrissionPage import ChromiumPage, ChromiumOptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("bytedance_sniffer")

BASE_URL = "https://jobs.bytedance.com/"
SEARCH_KEYWORD = "AI产品经理"
PAGE_SIZE = 50
LISTEN_TIMEOUT = 15
PAGE_WAIT = 5
OUTPUT_FILE = Path(__file__).parent / "bytedance_jobs.json"


def init_browser() -> ChromiumPage:
    co = ChromiumOptions()
    co.set_local_port(9444)
    co.set_user_data_path('./.chrome_profile_bytedance')
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
        page.set.timeouts(base=30, page_load=60, script=30)
        logger.info("✅ 浏览器已启动 (port=9444)")
        return page
    except Exception as e:
        logger.error("❌ 浏览器初始化失败: %s", e)
        raise e


def _collect_api_responses(page: ChromiumPage, listen_timeout: int = 15) -> list[dict]:
    collected = []
    deadline = time.time() + listen_timeout
    while time.time() < deadline:
        try:
            remaining = max(0.5, deadline - time.time())
            r = page.listen.wait(timeout=remaining)
            if r is None or not r.response:
                continue
            url = r.request.url if r.request else ""
            method = r.request.method if r.request else ""
            status = r.response.status if r.response else 0
            skip_ext = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.css', '.woff', '.woff2', '.ttf', '.eot', '.map']
            if any(url.endswith(e) for e in skip_ext):
                continue
            body = None
            try:
                body = r.response.body
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except json.JSONDecodeError:
                        if len(body) > 500:
                            body = body[:500] + "..."
            except Exception:
                body = None
            entry = {"url": url, "method": method, "status": status, "body": body}
            collected.append(entry)
            if body and isinstance(body, (dict, list)):
                logger.info("📡 API: [%s] %s (status=%s)", method, url, status)
                if isinstance(body, dict):
                    logger.info("   → keys: %s", list(body.keys())[:10])
                    d = body.get("data")
                    if isinstance(d, dict):
                        logger.info("   → data keys: %s", list(d.keys())[:10])
                    elif isinstance(d, list):
                        logger.info("   → data 长度: %d", len(d))
        except Exception:
            continue
    return collected


def _extract_jobs(api_responses: list[dict]) -> list[dict]:
    all_jobs = []
    seen_ids = set()
    for entry in api_responses:
        body = entry.get("body")
        url = entry.get("url", "")
        if not isinstance(body, dict):
            continue
        if "search/job_post" not in url:
            continue
        data = body.get("data")
        if not isinstance(data, dict):
            continue
        for list_key in ["job_post_list", "list", "records", "content", "items", "data"]:
            job_list = data.get(list_key)
            if isinstance(job_list, list) and len(job_list) > 0:
                logger.info("📦 从 data.%s 提取到 %d 条", list_key, len(job_list))
                for item in job_list:
                    job = _normalize(item)
                    if job and job.get("id") not in seen_ids:
                        seen_ids.add(job.get("id"))
                        all_jobs.append(job)
                break
    return all_jobs


def _normalize(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    job_id = item.get("id") or item.get("jobId") or item.get("positionId") or item.get("applyId") or item.get("code") or ""
    title = item.get("title") or item.get("name") or item.get("jobName") or item.get("positionName") or ""
    if not title and not job_id:
        return None
    company = item.get("company") or item.get("companyName") or item.get("brandName") or "字节跳动"
    if not company.strip():
        company = "字节跳动"
    salary = item.get("salary") or item.get("salaryDesc") or item.get("salaryRange") or ""
    location = item.get("location") or item.get("city") or item.get("cityName") or item.get("workPlace") or item.get("city_code") or ""
    description = item.get("description") or item.get("detail") or item.get("jd") or item.get("jobDescription") or item.get("postDescription") or ""
    education = item.get("education") or item.get("degree") or item.get("degreeName") or item.get("degree_name") or ""
    experience = item.get("experience") or item.get("workExp") or item.get("workYear") or item.get("work_exp_name") or ""
    detail_url = item.get("url") or item.get("detailUrl") or item.get("applyUrl") or item.get("jobUrl") or ""
    if not detail_url and job_id:
        detail_url = f"https://jobs.bytedance.com/experienced/position/{job_id}"
    elif detail_url and not detail_url.startswith("http"):
        detail_url = f"https://jobs.bytedance.com{detail_url}"
    publish_time = item.get("publishTime") or item.get("publish_time") or item.get("createTime") or item.get("createdAt") or item.get("postDate") or ""
    category = item.get("category") or item.get("type") or item.get("jobCategory") or item.get("department") or item.get("department_name") or item.get("job_type_name") or ""
    recruitment_type = item.get("post_recruitment_name") or item.get("recruitmentType") or ""
    return {
        "id": str(job_id) if job_id else "",
        "title": str(title).strip(),
        "company": str(company).strip(),
        "salary": str(salary).strip(),
        "location": str(location).strip(),
        "education": str(education).strip(),
        "experience": str(experience).strip(),
        "description": str(description).strip()[:500] if description else "",
        "url": str(detail_url).strip(),
        "publish_time": str(publish_time).strip(),
        "category": str(category).strip(),
        "recruitment_type": str(recruitment_type).strip(),
        "platform": "字节跳动",
    }


def sniff_bytedance_api() -> list[dict]:
    page = None
    all_jobs = []

    try:
        page = init_browser()

        # 启动监听
        logger.info("🔍 启动 API 监听...")
        page.listen.start("jobs.bytedance.com/api/v1/search/job_post")

        # 访问首页
        logger.info("🌐 访问字节跳动招聘首页...")
        page.get(BASE_URL)
        time.sleep(PAGE_WAIT)

        # ── 策略1: 通过页面 JS 环境调用 API ──
        logger.info("📡 策略1: 在页面 JS 环境中调用内部 API...")

        # 尝试多种可能的 API 路径
        api_paths = [
            "/api/v1/search/job_post/list",
            "/api/v1/search/job_post/search",
            "/api/v1/search/job_post/query",
            "/api/v1/search/job_post/page",
            "/api/v1/search/job_post/position",
            "/api/v1/search/job_post/positions",
            "/api/v1/search/job_post/result",
            "/api/v1/search/job_post/results",
            "/api/v1/search/job_post/filter",
            "/api/v1/search/job_post/find",
            "/api/v1/search/job_post/getList",
            "/api/v1/search/job_post/get_list",
            "/api/v1/search/job_post/load",
            "/api/v1/search/job_post/fetch",
            "/api/v1/search/job_post/select",
            "/api/v1/search/job_post/queryList",
            "/api/v1/search/job_post/query_list",
            "/api/v1/search/job_post/searchList",
            "/api/v1/search/job_post/search_list",
            "/api/v1/search/job_post/searchResult",
            "/api/v1/search/job_post/search_result",
            "/api/v1/search/job_post/searchPosition",
            "/api/v1/search/job_post/search_position",
            "/api/v1/search/job_post/searchJob",
            "/api/v1/search/job_post/search_job",
            "/api/v1/search/job_post/searchJobPost",
            "/api/v1/search/job_post/search_job_post",
            "/api/v1/search/job_post/getJobPostList",
            "/api/v1/search/job_post/get_job_post_list",
            "/api/v1/search/job_post/getJobList",
            "/api/v1/search/job_post/get_job_list",
            "/api/v1/search/job_post/getPositionList",
            "/api/v1/search/job_post/get_position_list",
            "/api/v1/search/job_post/getPostList",
            "/api/v1/search/job_post/get_post_list",
            "/api/v1/search/job_post/getPosts",
            "/api/v1/search/job_post/get_posts",
            "/api/v1/search/job_post/getPositions",
            "/api/v1/search/job_post/get_positions",
            "/api/v1/search/job_post/getJobs",
            "/api/v1/search/job_post/get_jobs",
            "/api/v1/search/job_post/getAll",
            "/api/v1/search/job_post/get_all",
            "/api/v1/search/job_post/getAllPosts",
            "/api/v1/search/job_post/get_all_posts",
            "/api/v1/search/job_post/getAllPositions",
            "/api/v1/search/job_post/get_all_positions",
            "/api/v1/search/job_post/getAllJobs",
            "/api/v1/search/job_post/get_all_jobs",
            "/api/v1/search/job_post/queryJobPost",
            "/api/v1/search/job_post/query_job_post",
            "/api/v1/search/job_post/queryJobPostList",
            "/api/v1/search/job_post/query_job_post_list",
            "/api/v1/search/job_post/queryPosition",
            "/api/v1/search/job_post/query_position",
            "/api/v1/search/job_post/queryPositionList",
            "/api/v1/search/job_post/query_position_list",
            "/api/v1/search/job_post/queryJob",
            "/api/v1/search/job_post/query_job",
            "/api/v1/search/job_post/queryJobList",
            "/api/v1/search/job_post/query_job_list",
            "/api/v1/search/job_post/queryPosts",
            "/api/v1/search/job_post/query_posts",
            "/api/v1/search/job_post/queryPositions",
            "/api/v1/search/job_post/query_positions",
            "/api/v1/search/job_post/queryJobs",
            "/api/v1/search/job_post/query_jobs",
            "/api/v1/search/job_post/queryAll",
            "/api/v1/search/job_post/query_all",
            "/api/v1/search/job_post/queryAllPosts",
            "/api/v1/search/job_post/query_all_posts",
            "/api/v1/search/job_post/queryAllPositions",
            "/api/v1/search/job_post/query_all_positions",
            "/api/v1/search/job_post/queryAllJobs",
            "/api/v1/search/job_post/query_all_jobs",
            "/api/v1/search/job_post/findJobPost",
            "/api/v1/search/job_post/find_job_post",
            "/api/v1/search/job_post/findJobPostList",
            "/api/v1/search/job_post/find_job_post_list",
            "/api/v1/search/job_post/findPosition",
            "/api/v1/search/job_post/find_position",
            "/api/v1/search/job_post/findPositionList",
            "/api/v1/search/job_post/find_position_list",
            "/api/v1/search/job_post/findJob",
            "/api/v1/search/job_post/find_job",
            "/api/v1/search/job_post/findJobList",
            "/api/v1/search/job_post/find_job_list",
            "/api/v1/search/job_post/findPosts",
            "/api/v1/search/job_post/find_posts",
            "/api/v1/search/job_post/findPositions",
            "/api/v1/search/job_post/find_positions",
            "/api/v1/search/job_post/findJobs",
            "/api/v1/search/job_post/find_jobs",
            "/api/v1/search/job_post/findAll",
            "/api/v1/search/job_post/find_all",
            "/api/v1/search/job_post/findAllPosts",
            "/api/v1/search/job_post/find_all_posts",
            "/api/v1/search/job_post/findAllPositions",
            "/api/v1/search/job_post/find_all_positions",
            "/api/v1/search/job_post/findAllJobs",
            "/api/v1/search/job_post/find_all_jobs",
            "/api/v1/search/job_post/getJobPost",
            "/api/v1/search/job_post/get_job_post",
            "/api/v1/search/job_post/getPosition",
            "/api/v1/search/job_post/get_position",
            "/api/v1/search/job_post/getJob",
            "/api/v1/search/job_post/get_job",
            "/api/v1/search/job_post/getPost",
            "/api/v1/search/job_post/get_post",
            "/api/v1/search/job_post/getDetail",
            "/api/v1/search/job_post/get_detail",
            "/api/v1/search/job_post/getDetails",
            "/api/v1/search/job_post/get_details",
            "/api/v1/search/job_post/getJobDetail",
            "/api/v1/search/job_post/get_job_detail",
            "/api/v1/search/job_post/getPositionDetail",
            "/api/v1/search/job_post/get_position_detail",
            "/api/v1/search/job_post/getPostDetail",
            "/api/v1/search/job_post/get_post_detail",
            "/api/v1/search/job_post/getJobDetails",
            "/api/v1/search/job_post/get_job_details",
            "/api/v1/search/job_post/getPositionDetails",
            "/api/v1/search/job_post/get_position_details",
            "/api/v1/search/job_post/getPostDetails",
            "/api/v1/search/job_post/get_post_details",
            "/api/v1/search/job_post/queryDetail",
            "/api/v1/search/job_post/query_detail",
            "/api/v1/search/job_post/queryDetails",
            "/api/v1/search/job_post/query_details",
            "/api/v1/search/job_post/queryJobDetail",
            "/api/v1/search/job_post/query_job_detail",
            "/api/v1/search/job_post/queryPositionDetail",
            "/api/v1/search/job_post/query_position_detail",
            "/api/v1/search/job_post/queryPostDetail",
            "/api/v1/search/job_post/query_post_detail",
            "/api/v1/search/job_post/queryJobDetails",
            "/api/v1/search/job_post/query_job_details",
            "/api/v1/search/job_post/queryPositionDetails",
            "/api/v1/search/job_post/query_position_details",
            "/api/v1/search/job_post/queryPostDetails",
            "/api/v1/search/job_post/query_post_details",
            "/api/v1/search/job_post/findDetail",
            "/api/v1/search/job_post/find_detail",
            "/api/v1/search/job_post/findDetails",
            "/api/v1/search/job_post/find_details",
            "/api/v1/search/job_post/findJobDetail",
            "/api/v1/search/job_post/find_job_detail",
            "/api/v1/search/job_post/findPositionDetail",
            "/api/v1/search/job_post/find_position_detail",
            "/api/v1/search/job_post/findPostDetail",
            "/api/v1/search/job_post/find_post_detail",
            "/api/v1/search/job_post/findJobDetails",
            "/api/v1/search/job_post/find_job_details",
            "/api/v1/search/job_post/findPositionDetails",
            "/api/v1/search/job_post/find_position_details",
            "/api/v1/search/job_post/findPostDetails",
            "/api/v1/search/job_post/find_post_details",
        ]

        request_body = {
            "job_type_id": "",
            "job_function_id": "",
            "city_code": "",
            "industry_code": "",
            "tag_code": "",
            "search_keyword": SEARCH_KEYWORD,
            "sort_type": 0,
            "page_index": 1,
            "page_size": PAGE_SIZE,
            "offset": 0,
            "recruitment_type": "social"
        }

        # 分批测试 API 路径（每次 5 个）
        batch_size = 5
        for i in range(0, len(api_paths), batch_size):
            batch = api_paths[i:i+batch_size]
            js_code = f"""
            (async () => {{
                const results = [];
                const paths = {json.dumps(batch)};
                const body = {json.dumps(request_body, ensure_ascii=False)};
                for (const path of paths) {{
                    try {{
                        const resp = await fetch(path, {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
                            body: JSON.stringify(body)
                        }});
                        const text = await resp.text();
                        results.push({{ path, status: resp.status, text: text.substring(0, 200) }});
                    }} catch (e) {{
                        results.push({{ path, error: e.message }});
                    }}
                }}
                return results;
            }})()
            """
            try:
                results = page.run_js(js_code)
                if results:
                    for r in results:
                        status = r.get("status", "error")
                        path = r.get("path", "")
                        if status == 200:
                            logger.info("   ✅ %s → 200", path)
                        elif status == 404:
                            pass  # 静默跳过 404
                        else:
                            logger.info("   ⚠️  %s → %s", path, status)
            except Exception as e:
                logger.warning("   JS 执行失败 (batch %d): %s", i//batch_size, e)

            time.sleep(0.5)

        # 等待所有 API 响应
        logger.info("⏳ 等待 API 响应...")
        time.sleep(3)

        # 收集拦截到的 API 响应
        logger.info("📥 收集 API 响应...")
        api_responses = _collect_api_responses(page, listen_timeout=LISTEN_TIMEOUT)
        logger.info("📊 共拦截到 %d 个 API 请求/响应", len(api_responses))

        try:
            page.listen.stop()
        except Exception:
            pass

        # 提取岗位数据
        logger.info("🔎 提取岗位数据...")
        all_jobs = _extract_jobs(api_responses)
        logger.info("✅ 提取到 %d 条岗位数据", len(all_jobs))

        # 保存
        output = {
            "timestamp": datetime.now().isoformat(),
            "target_url": BASE_URL,
            "search_keyword": SEARCH_KEYWORD,
            "total_api_intercepted": len(api_responses),
            "total_jobs_extracted": len(all_jobs),
            "api_responses": api_responses,
            "jobs": all_jobs,
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        logger.info("💾 数据已保存到: %s", OUTPUT_FILE)

    except Exception as e:
        logger.error("❌ 脚本运行失败: %s", e, exc_info=True)
    finally:
        if page is not None:
            try:
                page.quit()
                logger.info("✅ 浏览器已关闭")
            except Exception:
                pass

    return all_jobs


def print_results(jobs: list[dict], api_responses: list[dict]):
    print("\n" + "=" * 70)
    print("📊 字节跳动招聘 API 嗅探结果")
    print("=" * 70)

    print(f"\n📡 拦截到的 API 端点 ({len(api_responses)} 个):")
    print("-" * 70)
    api_count = 0
    for entry in api_responses:
        url = entry.get("url", "")
        method = entry.get("method", "")
        status = entry.get("status", 0)
        if any(ext in url for ext in ['.js', '.css', '.png', '.jpg', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot']):
            continue
        api_count += 1
        short_url = url[:120] + "..." if len(url) > 120 else url
        print(f"  [{api_count}] {method} {status} | {short_url}")

    print(f"\n💼 提取到的岗位数据 ({len(jobs)} 条):")
    print("=" * 70)

    if not jobs:
        print("  ⚠️  未提取到岗位数据")
        print()
        print("  可能的原因:")
        print("    1. 字节跳动的招聘 API 路径与预期不同")
        print("    2. API 需要特定的认证头或 token")
        print("    3. 页面需要登录才能查看岗位列表")
        print()
        print("  💡 建议: 手动在浏览器中打开 https://jobs.bytedance.com/")
        print("     打开开发者工具 → Network 标签 → 过滤 XHR/Fetch")
        print("     查看实际加载岗位列表时调用的 API 路径和请求格式")
    else:
        for idx, job in enumerate(jobs, 1):
            print(f"\n  [{idx:2d}] {job['title']}")
            print(f"       公司: {job['company']}")
            print(f"       薪资: {job['salary']}")
            print(f"       地点: {job['location']}")
            print(f"       学历: {job['education']}")
            print(f"       经验: {job['experience']}")
            print(f"       类别: {job['category']}")
            if job['url']:
                print(f"       链接: {job['url']}")
            if job['description']:
                print(f"       描述: {job['description'][:200]}...")

    print("\n" + "=" * 70)
    print(f"💾 完整数据已保存到: {OUTPUT_FILE}")
    print("=" * 70)


def main():
    print("\n" + "=" * 70)
    print("🔍 字节跳动招聘 API 嗅探器 v5.0")
    print("=" * 70)
    print(f"  目标: {BASE_URL}")
    print(f"  关键词: {SEARCH_KEYWORD}")
    print(f"  输出: {OUTPUT_FILE}")
    print("=" * 70)

    jobs = sniff_bytedance_api()

    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print_results(data.get("jobs", []), data.get("api_responses", []))
    else:
        print_results(jobs, [])


if __name__ == "__main__":
    main()
