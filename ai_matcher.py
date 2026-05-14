"""
AI 匹配评估模块

接收岗位原始信息，调用 DeepSeek API 进行 5 维评分，
返回结构化的 JSON 评估结果。

评分维度：
  - 匹配度 (30%)：技能、经验与候选人（AI PM，4 年交易系统开发经验）的匹配度
  - 薪资   (25%)：薪资水平评估
  - 地点   (15%)：工作地点评估（优先远程或一线城市）
  - 发展   (15%)：职业成长空间
  - 团队   (15%)：公司阶段、团队文化
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)

# ── 候选人画像 ──

CANDIDATE_PROFILE = """
## 候选人画像
- 岗位：AI 产品经理（AI PM）
- 工作经验：4 年交易系统开发经验 + AI 产品经验
- 技能：大模型应用、AI 产品设计、Prompt Engineering、RAG、Agent、Python、前端开发
- 偏好方向：AI 产品经理、大模型应用、AI 中台、对话式 AI
- 期望薪资：30-60K
- 偏好地点：北京/上海/深圳/远程
"""

# ── System Prompt ──

SYSTEM_PROMPT = """你是一位专业的 AI 招聘顾问，负责评估岗位与候选人的匹配度。

请严格按照以下 5 个维度对岗位进行评分（满分 100 分）：

1. **匹配度 (30%)**：岗位职责、技能要求与候选人经验的匹配程度
2. **薪资 (25%)**：薪资范围与候选人期望的匹配程度
3. **地点 (15%)**：工作地点是否优先（远程 > 一线城市 > 其他）
4. **发展 (15%)**：职业成长空间、赛道前景
5. **团队 (15%)**：公司阶段、团队文化、平台价值

评分规则：
- 90-100：强烈推荐，高度匹配
- 80-89：推荐，匹配度良好
- 60-79：可以考虑，有一定匹配
- <60：不推荐，匹配度低

你必须返回严格的 JSON 格式（不要包含 markdown 代码块标记），格式如下：
{"score": <0-100整数>, "match_reasons": ["优势1", "优势2", ...], "mismatch_reasons": ["不足1", "不足2", ...], "summary": "<一句话总结>", "jd_summary_structured": "<结构化摘要>"}

关于 jd_summary_structured 字段的要求：
- 请综合「完整职位描述」和「职位要求」两部分文本，提炼一段约 100 字的结构化摘要
- 摘要格式必须统一为：
  🎯 核心职责：[用一两句话总结核心要干嘛]
  💡 硬性要求：[提炼最核心的年限、技术栈或大模型经验要求]
- 如果「完整职位描述」或「职位要求」为空，则根据已有信息尽力提炼"""


def evaluate_job(
    title: str,
    company: str,
    salary: str = "",
    location: str = "",
    platform: str = "",
    jd_summary: str = "",
    full_jd: str = "",
    requirements: str = "",
) -> dict[str, Any]:
    """
    评估一个岗位与候选人的匹配度。

    参数:
        title:        岗位名称
        company:      公司名称
        salary:       薪资范围（如 "35-65K·15薪"）
        location:     工作地点（如 "北京"）
        platform:     来源平台（如 "BOSS直聘"）
        jd_summary:   职位描述摘要
        full_jd:      完整职位描述（Deep Crawl 获取）
        requirements: 完整职位要求（Deep Crawl 获取）

    返回:
        {"score": int, "match_reasons": list[str], "mismatch_reasons": list[str], "summary": str, "jd_summary_structured": str}

    异常:
        当 API 调用失败或返回无法解析时，返回 {"score": 0, "match_reasons": [], "mismatch_reasons": [], "summary": "评估失败"}
    """
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY 未设置")
        return _fallback_result("API Key 未配置")

    # 构建岗位信息（包含完整 JD 和 Requirements）
    job_info = f"""
## 岗位信息
- 岗位名称：{title}
- 公司：{company}
- 薪资：{salary}
- 地点：{location}
- 来源平台：{platform}
- 职位描述：{jd_summary or "（暂无详细描述）"}
- 完整职位描述：{full_jd or "（无）"}
- 职位要求：{requirements or "（无）"}
"""

    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": CANDIDATE_PROFILE},
                {"role": "user", "content": f"请评估以下岗位与候选人的匹配度：\n{job_info}"},
            ],
            temperature=0.3,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if not content:
            logger.warning("AI 返回内容为空 [%s - %s]", company, title)
            return _fallback_result("AI 返回为空")

        return _parse_response(content, company, title)

    except Exception as e:
        logger.error("AI 评估失败 [%s - %s]: %s", company, title, e)
        return _fallback_result(str(e))


def _parse_response(content: str, company: str, title: str) -> dict[str, Any]:
    """解析 AI 返回的 JSON 字符串"""
    try:
        # 尝试直接解析
        result = json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取 JSON 部分（可能包含 markdown 代码块）
        try:
            # 查找 ```json ... ``` 或 ``` ... ```
            import re
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
            if match:
                result = json.loads(match.group(1))
            else:
                # 尝试查找最外层的 { ... }
                match = re.search(r"\{[\s\S]*\}", content)
                if match:
                    result = json.loads(match.group(0))
                else:
                    raise ValueError("无法从响应中提取 JSON")
        except Exception as e:
            logger.error("AI 响应解析失败 [%s - %s]: %s\n原始响应: %s", company, title, e, content[:200])
            return _fallback_result("无法从 AI 响应中解析 JSON")

    # 验证并规范化返回结果
    score = result.get("score", 0)
    if not isinstance(score, (int, float)):
        score = 0
    score = max(0, min(100, int(score)))

    match_reasons = result.get("match_reasons", [])
    if not isinstance(match_reasons, list):
        match_reasons = []

    mismatch_reasons = result.get("mismatch_reasons", [])
    if not isinstance(mismatch_reasons, list):
        mismatch_reasons = []

    summary = result.get("summary", "")
    if not isinstance(summary, str):
        summary = ""

    # 提取结构化摘要（AI 根据 full_jd + requirements 生成）
    jd_summary_structured = result.get("jd_summary_structured", "")
    if not isinstance(jd_summary_structured, str):
        jd_summary_structured = ""

    logger.info(
        "AI 评估完成: %s - %s 分 (%s)",
        title,
        score,
        summary[:50] if summary else "无总结",
    )

    return {
        "score": score,
        "match_reasons": match_reasons,
        "mismatch_reasons": mismatch_reasons,
        "summary": summary,
        "jd_summary_structured": jd_summary_structured,
    }


def _fallback_result(reason: str = "评估失败") -> dict[str, Any]:
    """返回默认的失败结果"""
    return {
        "score": 0,
        "match_reasons": [],
        "mismatch_reasons": [],
        "summary": reason,
        "jd_summary_structured": "",
    }


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 模拟输入：字节跳动 AI PM 岗位
    test_job = {
        "title": "AI 产品经理（大模型应用方向）",
        "company": "字节跳动",
        "salary": "35-65K·15薪",
        "location": "北京",
        "platform": "BOSS直聘",
        "jd_summary": "负责大模型在抖音、今日头条等核心产品的落地应用，设计AI驱动的产品功能，推动大模型技术从实验室走向亿级用户场景。需要深入理解大模型能力边界，结合业务需求定义产品方向。",
    }

    print("=" * 60)
    print("🧪 AI 匹配评估测试")
    print("=" * 60)
    print(f"岗位: {test_job['title']}")
    print(f"公司: {test_job['company']}")
    print(f"薪资: {test_job['salary']}")
    print(f"地点: {test_job['location']}")
    print(f"平台: {test_job['platform']}")
    print("-" * 60)

    result = evaluate_job(**test_job)

    print("\n📊 评估结果:")
    print(f"  评分: {result['score']}/100")
    print(f"  总结: {result['summary']}")
    if result["match_reasons"]:
        print(f"  ✅ 优势:")
        for reason in result["match_reasons"]:
            print(f"    - {reason}")
    if result["mismatch_reasons"]:
        print(f"  ⚠️  不足:")
        for reason in result["mismatch_reasons"]:
            print(f"    - {reason}")
    print("=" * 60)
