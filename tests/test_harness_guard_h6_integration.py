"""H6 集成测试：验证护栏在真实环境中拦截非法 plan

这个测试需要手动构造非法 plan，直接调用 check_plan 进行校验，
验证在生产环境（Skill Registry 非空）时护栏能正确拦截。

注意：这个测试不需要真实的 MCP 连接，只需要 Mock Registry 有工具即可。
"""
import sys
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.harness_guard import check_plan, GuardResult


# ==================== Mock Registry（模拟生产环境）====================

class MockSkill:
    """Mock Skill 对象"""
    def __init__(self, skill_id: str, name: str = ""):
        self.id = skill_id
        self.name = name or skill_id


class MockRegistry:
    """Mock Skill Registry，用于单元测试"""
    def __init__(self, skill_ids: list[str]):
        self.skills = [MockSkill(sid) for sid in skill_ids]

    def list_skills(self):
        return self.skills


def get_prod_mock_registry():
    """返回包含常用工具的 Mock Registry（模拟生产环境，非空）"""
    common_tools = [
        "ai_event_list",
        "ai_event_detail",
        "aggregate_alarms",
        "visualize_alarms",
        "vlm_judge_alarm",
        "update_alarm_status",
        "fetch_alarm_context",
        "video_device_list",
        "kb_regulation",
    ]
    return MockRegistry(common_tools)


# ==================== 集成测试用例 ====================

def test_integration_invalid_tool_denied():
    """集成测试 1：生产环境中，引用不存在的工具应被拦截"""
    print("\n[集成测试 1] 生产环境：引用不存在的工具")

    registry = get_prod_mock_registry()

    # 用户问："查询不存在工具的告警"
    # Planner 错误规划：引用假工具
    bad_plan = [
        {"task": "non_exist_tool", "args": {}, "status": "pending"}
    ]

    result = check_plan(bad_plan, registry)

    # 验证：应被拦截
    assert not result.allow, "生产环境中，假工具应被拦截"
    assert result.action == "deny"
    assert "non_exist_tool" in result.reason
    assert "不存在的工具" in result.reason

    print(f"  ✅ 拦截成功")
    print(f"  拦截原因: {result.reason[:80]}...")


def test_integration_invalid_event_type_denied():
    """集成测试 2：生产环境中，非法 event_type 应被拦截"""
    print("\n[集成测试 2] 生产环境：非法 event_type")

    registry = get_prod_mock_registry()

    # 用户问："查询类型为 FAKE_TYPE 的告警"
    # Planner 错误规划：使用非法 event_type
    bad_plan = [
        {"task": "ai_event_list", "args": {"event_type": "FAKE_TYPE"}, "status": "pending"}
    ]

    result = check_plan(bad_plan, registry)

    # 验证：应被拦截
    assert not result.allow, "非法 event_type 应被拦截"
    assert result.action == "deny"
    assert "FAKE_TYPE" in result.reason
    assert "无效的告警类型编码" in result.reason

    print(f"  ✅ 拦截成功")
    print(f"  拦截原因: {result.reason[:80]}...")


def test_integration_pagesize_auto_fix():
    """集成测试 3：生产环境中，pagesize 越界应自动修正"""
    print("\n[集成测试 3] 生产环境：pagesize 越界自动修正")

    registry = get_prod_mock_registry()

    # 用户问："查询 99999 条告警"
    # Planner 错误规划：pagesize 异常大
    bad_plan = [
        {"task": "ai_event_list", "args": {"pagesize": 99999}, "status": "pending"}
    ]

    result = check_plan(bad_plan, registry)

    # 验证：应放行但自动修正
    assert result.allow, "pagesize 越界应放行（自动修正）"
    assert result.fixed_plan is not None, "应返回修正后的 plan"
    assert result.fixed_plan[0]["args"]["pagesize"] == 100, "应被修正为 100"

    print(f"  ✅ 自动修正成功")
    print(f"  原始 pagesize: 99999 → 修正后: 100")


def test_integration_valid_plan_pass():
    """集成测试 4：生产环境中，合法 plan 应放行"""
    print("\n[集成测试 4] 生产环境：合法 plan 放行")

    registry = get_prod_mock_registry()

    # 用户问："查询未戴安全帽的告警"
    # Planner 正确规划：合法工具 + 合法 event_type
    good_plan = [
        {"task": "ai_event_list", "args": {"event_type": "ET03007", "pagesize": 10}, "status": "pending"}
    ]

    result = check_plan(good_plan, registry)

    # 验证：应放行
    assert result.allow, "合法 plan 应放行"
    assert result.fixed_plan is None, "合法 plan 不应触发修正"

    print(f"  ✅ 放行成功（护栏对合法 plan 零影响）")


def test_integration_multi_step_second_invalid():
    """集成测试 5：生产环境中，多步骤 plan 的第二步非法应被拦截"""
    print("\n[集成测试 5] 生产环境：多步骤 plan 第二步非法")

    registry = get_prod_mock_registry()

    # 第一步合法，第二步非法 event_type
    bad_plan = [
        {"task": "ai_event_list", "args": {"event_type": "ET03007"}, "status": "pending"},
        {"task": "ai_event_list", "args": {"event_type": "INVALID_CODE"}, "status": "pending"},
    ]

    result = check_plan(bad_plan, registry)

    # 验证：应在第二步被拦截
    assert not result.allow, "第二步非法应被拦截"
    assert "第 2 步" in result.reason, "reason 应指明是第 2 步"
    assert "INVALID_CODE" in result.reason

    print(f"  ✅ 拦截成功（在第 2 步被拦截）")
    print(f"  拦截原因: {result.reason[:80]}...")


def test_integration_empty_registry_skip_tool_check():
    """集成测试 6：测试环境（空 registry）跳过工具存在性校验"""
    print("\n[集成测试 6] 测试环境：空 registry 跳过工具校验")

    # 空 registry（模拟测试/评估环境）
    empty_registry = MockRegistry([])

    # 引用假工具（在生产环境会被拦截）
    plan = [
        {"task": "fake_tool", "args": {}, "status": "pending"}
    ]

    result = check_plan(plan, empty_registry)

    # 验证：应放行（因为 registry 为空，跳过工具校验）
    assert result.allow, "测试环境应跳过工具存在性校验"

    print(f"  ✅ 放行成功（测试环境存疑放行）")


def test_integration_empty_registry_event_type_still_check():
    """集成测试 7：测试环境（空 registry）仍然校验 event_type"""
    print("\n[集成测试 7] 测试环境：仍然校验 event_type")

    # 空 registry（模拟测试/评估环境）
    empty_registry = MockRegistry([])

    # 非法 event_type（无论哪个环境都应拦截）
    plan = [
        {"task": "ai_event_list", "args": {"event_type": "FAKE_TYPE"}, "status": "pending"}
    ]

    result = check_plan(plan, empty_registry)

    # 验证：应被拦截（event_type 校验在任何环境都生效）
    assert not result.allow, "即使测试环境，非法 event_type 也应拦截"
    assert "FAKE_TYPE" in result.reason

    print(f"  ✅ 拦截成功（event_type 校验不受环境影响）")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 70)
    print("H6 集成测试：护栏在真实环境中的拦截行为")
    print("=" * 70)

    tests = [
        test_integration_invalid_tool_denied,
        test_integration_invalid_event_type_denied,
        test_integration_pagesize_auto_fix,
        test_integration_valid_plan_pass,
        test_integration_multi_step_second_invalid,
        test_integration_empty_registry_skip_tool_check,
        test_integration_empty_registry_event_type_still_check,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ 测试失败: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ 测试异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"集成测试结果：✅ {passed} 通过，❌ {failed} 失败")
    print("=" * 70)

    if failed == 0:
        print("\n🎉 所有集成测试通过！护栏在生产环境中能正确拦截非法 plan。")
    else:
        print(f"\n⚠️ 有 {failed} 个测试失败，请检查护栏实现。")

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)
