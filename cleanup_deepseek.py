#!/Users/gmx/opt/anaconda3/envs/job_env/bin/python
"""
cleanup_deepseek.py — 清理 Notion 中 DeepSeek 的旧脏数据

删除 Notion JobMonitor 数据库中所有 Company 为 'DeepSeek' 的页面。
这些数据是之前从大池子里抓到的会计、行政等非 AI PM 岗位。

用法:
  conda run -n job_env python cleanup_deepseek.py
  conda run -n job_env python cleanup_deepseek.py --dry-run   # 仅预览，不删除
"""

import sys, io, logging, argparse

# ── UTF-8 输出 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")

# 直接从 config 导入
sys.path.insert(0, ".")
from config import NOTION_API_KEY, NOTION_JOBS_DB

import requests

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"


def notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def query_deepseek_pages() -> list[dict]:
    """
    查询 Notion 数据库中所有 Company 包含 'DeepSeek' 的页面。
    返回 page 列表（含 id 和 properties）。
    """
    headers = notion_headers()
    all_pages = []
    has_more = True
    start_cursor = None

    while has_more:
        body = {
            "filter": {
                "property": "Company",
                "rich_text": {"contains": "DeepSeek"},
            },
            "page_size": 100,
        }
        if start_cursor:
            body["start_cursor"] = start_cursor

        try:
            resp = requests.post(
                f"{NOTION_BASE_URL}/databases/{NOTION_JOBS_DB}/query",
                headers=headers,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("results", [])
            all_pages.extend(pages)
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
            logger.info(f"  查询到 {len(pages)} 条 DeepSeek 记录 (累计 {len(all_pages)})")
        except Exception as e:
            logger.error(f"查询失败: {e}")
            break

    return all_pages


def archive_page(page_id: str) -> bool:
    """
    归档 Notion 页面（设置 archived=true）。
    Notion API 不支持 DELETE，需要使用 PATCH 设置 archived 状态。
    """
    headers = notion_headers()
    try:
        resp = requests.patch(
            f"{NOTION_BASE_URL}/pages/{page_id}",
            headers=headers,
            json={"archived": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"  归档 page {page_id} 失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="清理 Notion 中 DeepSeek 的旧脏数据")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不删除")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("🧹 清理 Notion 中 DeepSeek 旧脏数据")
    print("=" * 60)

    if not NOTION_API_KEY or not NOTION_JOBS_DB:
        logger.error("❌ NOTION_API_KEY 或 NOTION_JOBS_DB 未配置")
        sys.exit(1)

    print(f"  Notion DB: {NOTION_JOBS_DB}")
    if args.dry_run:
        print("  🔍 DRY RUN 模式：仅预览，不删除")
    print("-" * 60)

    # 查询所有 DeepSeek 页面
    logger.info("🔍 查询 Notion 中所有 DeepSeek 记录...")
    pages = query_deepseek_pages()

    if not pages:
        logger.info("✅ 没有找到任何 DeepSeek 记录，无需清理")
        return

    print(f"\n📊 共找到 {len(pages)} 条 DeepSeek 记录：")
    print("-" * 60)

    for idx, page in enumerate(pages, 1):
        props = page.get("properties", {})
        title = ""
        try:
            title = props.get("Title", {}).get("title", [{}])[0].get("text", {}).get("content", "")
        except Exception:
            title = "<无法获取标题>"

        company = ""
        try:
            company = props.get("Company", {}).get("rich_text", [{}])[0].get("text", {}).get("content", "")
        except Exception:
            company = "DeepSeek"

        url = ""
        try:
            url = props.get("URL", {}).get("url", "") or ""
        except Exception:
            url = ""

        page_id = page["id"]
        print(f"  [{idx:2d}] {title} @ {company}")
        print(f"        ID: {page_id}")
        print(f"        URL: {url[:80]}...")

        if not args.dry_run:
            ok = archive_page(page_id)
            if ok:
                print(f"        ✅ 已归档")
            else:
                print(f"        ❌ 归档失败")
        else:
            print(f"        🔍 DRY RUN: 跳过删除")

    print("-" * 60)
    if args.dry_run:
        print(f"\n🔍 DRY RUN 完成：共 {len(pages)} 条记录将被归档")
        print("   运行不带 --dry-run 参数以实际执行归档")
    else:
        print(f"\n✅ 清理完成！共归档 {len(pages)} 条 DeepSeek 旧数据")


if __name__ == "__main__":
    main()
