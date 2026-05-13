"""配置加载模块：环境变量 + 黑名单"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ── Notion ──
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_JOBS_DB = os.getenv("NOTION_JOBS_DB", "")

# ── DeepSeek ──
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ── Chrome CDP ──
CHROME_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")

# ── 目标 URL ──
TARGET_URL = os.getenv("TARGET_URL", "")

# ── 黑名单 ──
def load_blacklist() -> list[str]:
    """读取 blacklist.txt，返回公司名列表（去注释、去空行）"""
    path = Path(__file__).parent / "blacklist.txt"
    if not path.exists():
        return []
    names: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                names.append(stripped)
    return names

BLACKLIST = load_blacklist()

def is_blacklisted(company: str) -> bool:
    """检查公司名是否命中黑名单（部分匹配）"""
    if not company:
        return False
    company_lower = company.lower()
    for blocked in BLACKLIST:
        if blocked.lower() in company_lower:
            return True
    return False
