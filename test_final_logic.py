#!/usr/bin/env python3
"""
test_final_logic.py — 验证 notion_sync.py 和 bytedance_visual_crawler.py 的语法，
并测试 _build_jd_children 函数的 JD → Notion Block 转换逻辑。

用法:
    python test_final_logic.py
"""

import sys
import json

# ── 1. 语法检查 ──

def check_syntax(module_name: str, filepath: str) -> bool:
    """检查 Python 文件语法"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, filepath, "exec")
        print(f"  ✅ {module_name} 语法正确")
        return True
    except SyntaxError as e:
        print(f"  ❌ {module_name} 语法错误: {e}")
        return False


# ── 2. 导入测试 ──

def test_import_notion_sync():
    """测试 notion_sync.py 能否正常导入"""
    print("\n  ── 测试导入 notion_sync ──")
    try:
        # 模拟 config 模块（避免依赖真实 API KEY）
        import types
        config_mock = types.ModuleType("config")
        config_mock.NOTION_API_KEY = "test_key"
        config_mock.NOTION_JOBS_DB = "test_db"
        sys.modules["config"] = config_mock

        import notion_sync
        print(f"  ✅ 导入成功")
        print(f"     _build_jd_children = {notion_sync._build_jd_children}")
        print(f"     _list_to_bullets  = {notion_sync._list_to_bullets}")
        return notion_sync
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")
        return None


# ── 3. _build_jd_children 测试 ──

def test_build_jd_children(notion_sync):
    """测试 _build_jd_children 的 JD → Notion Block 转换"""
    print("\n  ── 测试 _build_jd_children ──")

    test_cases = [
        # case 0: 空文本
        {
            "name": "空文本",
            "job_description": "",
            "job_requirements": "",
            "expected_min_blocks": 0,
            "expected_max_blocks": 0,
        },
        # case 1: 只有职位描述
        {
            "name": "只有职位描述",
            "job_description": "负责 AI 产品的需求分析和产品规划。",
            "job_requirements": "",
            "expected_min_blocks": 2,  # heading_2 + paragraph
            "expected_max_blocks": 2,
            "check_heading_text": "🎯 职位描述",
        },
        # case 2: 只有职位要求
        {
            "name": "只有职位要求",
            "job_description": "",
            "job_requirements": "1. 本科及以上学历\n2. 3 年以上经验",
            "expected_min_blocks": 2,  # heading_2 + paragraph
            "expected_max_blocks": 2,
            "check_heading_text": "🛠️ 职位要求",
        },
        # case 3: 两者都有
        {
            "name": "两者都有",
            "job_description": "负责 AI 产品的需求分析和产品规划。\n与工程团队紧密合作。",
            "job_requirements": "1. 本科及以上学历\n2. 3 年以上产品经理经验",
            "expected_min_blocks": 4,  # 2 headings + 2 paragraphs
            "expected_max_blocks": 6,
            "check_headings": ["🎯 职位描述", "🛠️ 职位要求"],
        },
        # case 4: 长文本截断测试（超过 2000 字符）
        {
            "name": "长描述截断",
            "job_description": "A" * 2500,
            "job_requirements": "",
            "expected_min_blocks": 3,  # 1 heading + 2 paragraphs (split)
            "expected_max_blocks": 5,
        },
        # case 5: 两者都是长文本
        {
            "name": "两者长文本",
            "job_description": "B" * 3000,
            "job_requirements": "C" * 1500,
            "expected_min_blocks": 4,  # 2 headings + multi paragraphs
            "expected_max_blocks": 10,
            "check_headings": ["🎯 职位描述", "🛠️ 职位要求"],
        },
        # case 6: 带换行和空格的描述
        {
            "name": "带格式文本",
            "job_description": "  负责产品设计。  \n\n  与团队协作。  ",
            "job_requirements": "  1. 本科  \n  2. 硕士  ",
            "expected_min_blocks": 4,
            "expected_max_blocks": 6,
            "check_headings": ["🎯 职位描述", "🛠️ 职位要求"],
        },
    ]

    passed = 0
    failed = 0

    for i, tc in enumerate(test_cases):
        name = tc["name"]
        try:
            blocks = notion_sync._build_jd_children(
                job_description=tc["job_description"],
                job_requirements=tc["job_requirements"],
            )
            block_count = len(blocks)

            # 检查 block 数量范围
            min_b = tc["expected_min_blocks"]
            max_b = tc["expected_max_blocks"]
            if not (min_b <= block_count <= max_b):
                print(f"  ❌ [{i}] {name}: block 数量 {block_count} 不在预期范围 [{min_b}, {max_b}]")
                print(f"       blocks: {json.dumps(blocks, ensure_ascii=False, indent=2)[:500]}")
                failed += 1
                continue

            # 检查特定 heading 文本
            if tc.get("check_heading_text"):
                heading_texts = [b.get("heading_2", {}).get("rich_text", [{}])[0].get("text", {}).get("content", "") for b in blocks if b.get("type") == "heading_2"]
                if tc["check_heading_text"] not in heading_texts:
                    print(f"  ❌ [{i}] {name}: 期望 heading 包含 '{tc['check_heading_text']}'，实际 headings: {heading_texts}")
                    failed += 1
                    continue

            # 检查多个 heading
            if tc.get("check_headings"):
                heading_texts = [b.get("heading_2", {}).get("rich_text", [{}])[0].get("text", {}).get("content", "") for b in blocks if b.get("type") == "heading_2"]
                for h in tc["check_headings"]:
                    if h not in heading_texts:
                        print(f"  ❌ [{i}] {name}: 期望 heading 包含 '{h}'，实际 headings: {heading_texts}")
                        failed += 1
                        break
                else:
                    passed += 1
                    print(f"  ✅ [{i}] {name}: {block_count} blocks, headings={heading_texts}")
                    continue

            passed += 1
            print(f"  ✅ [{i}] {name}: {block_count} blocks")

        except Exception as e:
            print(f"  ❌ [{i}] {name}: 抛出异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    return passed, failed


# ── 4. _list_to_bullets 测试 ──

def test_list_to_bullets(notion_sync):
    """测试 _list_to_bullets 辅助函数"""
    print("\n  ── 测试 _list_to_bullets ──")

    cases = [
        ([], ""),
        (["A"], "• A"),
        (["A", "B"], "• A\n• B"),
        (["A", "B", "C"], "• A\n• B\n• C"),
    ]

    passed = 0
    for items, expected in cases:
        result = notion_sync._list_to_bullets(items)
        if result == expected:
            passed += 1
            print(f"  ✅ {items!r} → {result!r}")
        else:
            print(f"  ❌ {items!r} → {result!r} (期望 {expected!r})")

    return passed, len(cases) - passed


# ── 5. _build_properties 测试 ──

def test_build_properties(notion_sync):
    """测试 _build_properties 字段构建"""
    print("\n  ── 测试 _build_properties ──")

    try:
        props = notion_sync._build_properties(
            title="AI产品经理",
            company="字节跳动",
            platform="BOSS直聘",
            url="https://example.com/job/123",
            location="北京",
            remote="现场",
            salary_range="35-65K",
            jd_summary="负责AI产品",
            match_score=85,
            match_reasons=["经验匹配", "技能匹配"],
            mismatch_reasons=["行业不同"],
            status="新发现",
            priority="高",
            discovered_date="2026-05-14",
            notes="推荐投递",
        )

        # 验证关键字段
        checks = [
            ("Title", props.get("Title", {}).get("title", [{}])[0].get("text", {}).get("content") == "AI产品经理"),
            ("Company", props.get("Company", {}).get("rich_text", [{}])[0].get("text", {}).get("content") == "字节跳动"),
            ("Platform", props.get("Platform", {}).get("rich_text", [{}])[0].get("text", {}).get("content") == "BOSS直聘"),
            ("URL", props.get("URL", {}).get("url") == "https://example.com/job/123"),
            ("Match Score", props.get("Match Score", {}).get("number") == 85),
            ("Status", props.get("Status", {}).get("select", {}).get("name") == "新发现"),
            ("Priority", props.get("Priority", {}).get("select", {}).get("name") == "高"),
            ("Match Reasons", "经验匹配" in str(props.get("Match Reasons", {}))),
        ]

        all_ok = True
        for name, ok in checks:
            status = "✅" if ok else "❌"
            print(f"  {status} {name}")
            if not ok:
                all_ok = False

        if all_ok:
            print(f"  ✅ 所有字段验证通过")
            return 1, 0
        else:
            print(f"  ❌ 部分字段验证失败")
            return 0, 1

    except Exception as e:
        print(f"  ❌ 抛出异常: {e}")
        return 0, 1


# ── 6. bytedance_visual_crawler 语法检查 ──

def test_bytedance_syntax():
    """检查 bytedance_visual_crawler.py 语法"""
    print("\n  ── 检查 bytedance_visual_crawler.py 语法 ──")
    return check_syntax("bytedance_visual_crawler.py", "bytedance_visual_crawler.py")


# ── 7. openclaw_bridge 语法检查 ──

def test_openclaw_bridge_syntax():
    """检查 openclaw_bridge.py 语法"""
    print("\n  ── 检查 openclaw_bridge.py 语法 ──")
    return check_syntax("openclaw_bridge.py", "openclaw_bridge.py")


# ── 主流程 ──

def main():
    print("=" * 70)
    print("🧪 最终逻辑验证测试")
    print("=" * 70)

    # 1. 语法检查
    print("\n📌 1. 语法检查")
    syntax_ok = check_syntax("notion_sync.py", "notion_sync.py")
    if not syntax_ok:
        print("  ❌ notion_sync.py 语法错误，终止测试")
        sys.exit(1)

    syntax_bytedance = test_bytedance_syntax()
    syntax_bridge = test_openclaw_bridge_syntax()

    # 2. 导入测试
    print("\n📌 2. 导入测试")
    notion_sync = test_import_notion_sync()
    if notion_sync is None:
        print("  ❌ 导入失败，终止测试")
        sys.exit(1)

    # 3. 功能测试
    print("\n📌 3. 功能测试")

    total_passed = 0
    total_failed = 0

    # 3a. _build_jd_children
    p, f = test_build_jd_children(notion_sync)
    total_passed += p
    total_failed += f

    # 3b. _list_to_bullets
    p, f = test_list_to_bullets(notion_sync)
    total_passed += p
    total_failed += f

    # 3c. _build_properties
    p, f = test_build_properties(notion_sync)
    total_passed += p
    total_failed += f

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("📊 测试汇总")
    print("=" * 70)
    print(f"  语法检查:     {'✅ 通过' if syntax_ok else '❌ 失败'}")
    print(f"  导入测试:     {'✅ 通过' if notion_sync else '❌ 失败'}")
    print(f"  功能测试:     {total_passed} 通过, {total_failed} 失败")
    print(f"  总测试用例:   {total_passed + total_failed} 个")
    print("=" * 70)

    if total_failed > 0:
        print("❌ 部分测试失败，请检查上述日志")
        sys.exit(1)
    else:
        print("🎉 全部测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
