"""
job_model.py — 标准化岗位数据模型

定义所有爬虫统一输出的标准数据结构。
下游 ai_matcher / notion_sync 只依赖此模型，不关心数据来自哪个平台。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class JobItem:
    """
    标准化岗位数据字典。

    所有爬虫（spider_boss, spider_liepin 等）必须输出此格式。
    """
    # ── 必填字段 ──
    platform: str              # 来源平台：如 "BOSS直聘", "猎聘"
    job_name: str              # 岗位名称
    company: str               # 公司名称
    url: str                   # 岗位详情链接（去重主键）

    # ── 可选字段 ──
    salary: str = ""           # 薪资范围（如 "35-65K·15薪"）
    city: str = ""             # 工作地点（如 "北京"）
    jd_summary: str = ""       # 职位描述（原始文本，后续由 ai_matcher 截断）

    # ── 平台原始 ID（用于调试/去重） ──
    platform_job_id: str = ""  # 平台侧岗位 ID（如 encryptJobId）

    def to_dict(self) -> dict:
        """转为普通字典，兼容下游调用"""
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "JobItem":
        """从字典构建（兼容旧格式字段名映射）"""
        return JobItem(
            platform=d.get("platform", ""),
            job_name=d.get("jobName", d.get("job_name", "")),
            company=d.get("brandName", d.get("company", "")),
            salary=d.get("salaryDesc", d.get("salary", "")),
            city=d.get("cityName", d.get("city", "")),
            url=d.get("url", ""),
            jd_summary=d.get("jd_summary", ""),
            platform_job_id=d.get("encryptJobId", d.get("platform_job_id", "")),
        )
