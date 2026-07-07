"""任务 H6 单元测试：check_plan 三类校验

测试覆盖：
  1. 工具存在性校验：引用假工具 → deny
  2. event_type 合法性校验：带非法编码 → deny
  3. 参数越界自动修正：pagesize > 1000 → fixed_plan
  4. 合法 plan 放行：T0 用例应全部通过
"""
import sys
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.harness_guard import check_plan, GuardResult


# ==================== Mock Registry ====================

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


# 构造一个包含常用工具的 Mock Registry
def get_mock_registry():
    """返回包含常用工具的 Mock Registry"""
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


# ==================== 测试用例 ====================

def test_check_plan_invalid_tool():
    """测试 1：引用不存在的工具 → deny"""
    registry = get_mock_registry()

    # 构造引用假工具的 plan
    plan = [
        {"task": "fake_tool_not_exist", "args": {}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应被拦截
    assert not result.allow, "引用假工具应被拦截"
    assert result.action == "deny", "action 应为 deny"
    assert "fake_tool_not_exist" in result.reason, "reason 应包含工具名"
    assert "不存在的工具" in result.reason, "reason 应提示工具不存在"
    print("✅ 测试 1 通过：引用假工具被拦截")


def test_check_plan_invalid_event_type():
    """测试 2：带非法 event_type → deny"""
    registry = get_mock_registry()

    # 构造带非法 event_type 的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"event_type": "ET99999"},  # 不存在的编码
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应被拦截
    assert not result.allow, "非法 event_type 应被拦截"
    assert result.action == "deny", "action 应为 deny"
    assert "ET99999" in result.reason, "reason 应包含非法编码"
    assert "无效的告警类型编码" in result.reason, "reason 应提示编码无效"
    print("✅ 测试 2 通过：非法 event_type 被拦截")


def test_check_plan_pagesize_overflow():
    """测试 3：pagesize 越界 → 自动修正"""
    registry = get_mock_registry()

    # 构造 pagesize 越界的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"pagesize": 9999},  # 异常大
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应放行但返回修正后的 plan
    assert result.allow, "pagesize 越界应放行（自动修正）"
    assert result.fixed_plan is not None, "应返回修正后的 plan"
    assert len(result.fixed_plan) == 1, "修正后 plan 长度应为 1"
    assert result.fixed_plan[0]["args"]["pagesize"] == 100, "pagesize 应被修正为 100"
    print("✅ 测试 3 通过：pagesize 越界被自动修正为 100")


def test_check_plan_valid_plan():
    """测试 4：合法 plan → 放行"""
    registry = get_mock_registry()

    # 构造完全合法的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"event_type": "ET03007", "pagesize": 10},
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应放行，无修正
    assert result.allow, "合法 plan 应放行"
    assert result.action == "allow", "action 应为 allow"
    assert result.fixed_plan is None, "合法 plan 不应有 fixed_plan"
    print("✅ 测试 4 通过：合法 plan 放行")


def test_check_plan_special_tools():
    """测试 5：特殊工具（direct_response / stream_chat）→ 放行"""
    registry = get_mock_registry()

    # direct_response
    plan1 = [{"task": "direct_response", "args": {"text": "你好"}, "status": "pending"}]
    result1 = check_plan(plan1, registry)
    assert result1.allow, "direct_response 应放行"

    # stream_chat
    plan2 = [{"task": "stream_chat", "args": {}, "status": "pending"}]
    result2 = check_plan(plan2, registry)
    assert result2.allow, "stream_chat 应放行"

    print("✅ 测试 5 通过：特殊工具 direct_response / stream_chat 放行")


def test_check_plan_multi_step():
    """测试 6：多步骤 plan，第二步非法 → deny 并指明步骤"""
    registry = get_mock_registry()

    # 第一步合法，第二步工具不存在
    plan = [
        {"task": "ai_event_list", "args": {"event_type": "ET03007"}, "status": "pending"},
        {"task": "fake_tool", "args": {}, "status": "pending"},
    ]

    result = check_plan(plan, registry)

    # 验证：应在第二步被拦截
    assert not result.allow, "第二步非法应被拦截"
    assert "第 2 步" in result.reason, "reason 应指明是第 2 步"
    assert "fake_tool" in result.reason, "reason 应包含非法工具名"
    print("✅ 测试 6 通过：多步骤 plan 第二步非法被正确拦截")


def test_check_plan_edge_pagesize():
    """测试 7：边界值测试 - pagesize = 1000 不触发修正"""
    registry = get_mock_registry()

    # pagesize 刚好 1000（不越界）
    plan = [
        {"task": "ai_event_list", "args": {"pagesize": 1000}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应放行，不修正
    assert result.allow, "pagesize=1000 应放行"
    assert result.fixed_plan is None, "pagesize=1000 不应触发修正"
    print("✅ 测试 7 通过：pagesize=1000 边界值不触发修正")


def test_check_plan_edge_pagesize_1001():
    """测试 8：边界值测试 - pagesize = 1001 触发修正"""
    registry = get_mock_registry()

    # pagesize 刚好 1001（刚越界）
    plan = [
        {"task": "ai_event_list", "args": {"pagesize": 1001}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应放行但修正
    assert result.allow, "pagesize=1001 应放行"
    assert result.fixed_plan is not None, "pagesize=1001 应触发修正"
    assert result.fixed_plan[0]["args"]["pagesize"] == 100, "应被修正为 100"
    print("✅ 测试 8 通过：pagesize=1001 边界值触发修正")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("H6 单元测试：check_plan 三类校验")
    print("=" * 60)

    tests = [
        ("工具存在性校验", test_check_plan_invalid_tool),
        ("event_type 合法性校验", test_check_plan_invalid_event_type),
        ("pagesize 越界自动修正", test_check_plan_pagesize_overflow),
        ("合法 plan 放行", test_check_plan_valid_plan),
        ("特殊工具放行", test_check_plan_special_tools),
        ("多步骤非法拦截", test_check_plan_multi_step),
        ("边界值 pagesize=1000", test_check_plan_edge_pagesize),
        ("边界值 pagesize=1001", test_check_plan_edge_pagesize_1001),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"\n[{name}]")
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ 测试失败: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ 测试异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果：✅ {passed} 通过，❌ {failed} 失败")
    print("=" * 60)

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)


    # 构造引用假工具的 plan
    plan = [
        {"task": "fake_tool_not_exist", "args": {}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应被拦截
    assert not result.allow, "引用假工具应被拦截"
    assert result.action == "deny", "action 应为 deny"
    assert "fake_tool_not_exist" in result.reason, "reason 应包含工具名"
    assert "不存在的工具" in result.reason, "reason 应提示工具不存在"
    print("✅ 测试 1 通过：引用假工具被拦截")


def test_check_plan_invalid_event_type():
    """测试 2：带非法 event_type → deny"""
    registry = get_skill_registry()

    # 构造带非法 event_type 的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"event_type": "ET99999"},  # 不存在的编码
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应被拦截
    assert not result.allow, "非法 event_type 应被拦截"
    assert result.action == "deny", "action 应为 deny"
    assert "ET99999" in result.reason, "reason 应包含非法编码"
    assert "无效的告警类型编码" in result.reason, "reason 应提示编码无效"
    print("✅ 测试 2 通过：非法 event_type 被拦截")


def test_check_plan_pagesize_overflow():
    """测试 3：pagesize 越界 → 自动修正"""
    registry = get_skill_registry()

    # 构造 pagesize 越界的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"pagesize": 9999},  # 异常大
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应放行但返回修正后的 plan
    assert result.allow, "pagesize 越界应放行（自动修正）"
    assert result.fixed_plan is not None, "应返回修正后的 plan"
    assert len(result.fixed_plan) == 1, "修正后 plan 长度应为 1"
    assert result.fixed_plan[0]["args"]["pagesize"] == 100, "pagesize 应被修正为 100"
    print("✅ 测试 3 通过：pagesize 越界被自动修正为 100")


def test_check_plan_valid_plan():
    """测试 4：合法 plan → 放行"""
    registry = get_skill_registry()

    # 构造完全合法的 plan
    plan = [
        {
            "task": "ai_event_list",
            "args": {"event_type": "ET03007", "pagesize": 10},
            "status": "pending"
        }
    ]

    result = check_plan(plan, registry)

    # 验证：应放行，无修正
    assert result.allow, "合法 plan 应放行"
    assert result.action == "allow", "action 应为 allow"
    assert result.fixed_plan is None, "合法 plan 不应有 fixed_plan"
    print("✅ 测试 4 通过：合法 plan 放行")


def test_check_plan_special_tools():
    """测试 5：特殊工具（direct_response / stream_chat）→ 放行"""
    registry = get_skill_registry()

    # direct_response
    plan1 = [{"task": "direct_response", "args": {"text": "你好"}, "status": "pending"}]
    result1 = check_plan(plan1, registry)
    assert result1.allow, "direct_response 应放行"

    # stream_chat
    plan2 = [{"task": "stream_chat", "args": {}, "status": "pending"}]
    result2 = check_plan(plan2, registry)
    assert result2.allow, "stream_chat 应放行"

    print("✅ 测试 5 通过：特殊工具 direct_response / stream_chat 放行")


def test_check_plan_multi_step():
    """测试 6：多步骤 plan，第二步非法 → deny 并指明步骤"""
    registry = get_skill_registry()

    # 第一步合法，第二步工具不存在
    plan = [
        {"task": "ai_event_list", "args": {"event_type": "ET03007"}, "status": "pending"},
        {"task": "fake_tool", "args": {}, "status": "pending"},
    ]

    result = check_plan(plan, registry)

    # 验证：应在第二步被拦截
    assert not result.allow, "第二步非法应被拦截"
    assert "第 2 步" in result.reason, "reason 应指明是第 2 步"
    assert "fake_tool" in result.reason, "reason 应包含非法工具名"
    print("✅ 测试 6 通过：多步骤 plan 第二步非法被正确拦截")


def test_check_plan_edge_pagesize():
    """测试 7：边界值测试 - pagesize = 1000 不触发修正"""
    registry = get_skill_registry()

    # pagesize 刚好 1000（不越界）
    plan = [
        {"task": "ai_event_list", "args": {"pagesize": 1000}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应放行，不修正
    assert result.allow, "pagesize=1000 应放行"
    assert result.fixed_plan is None, "pagesize=1000 不应触发修正"
    print("✅ 测试 7 通过：pagesize=1000 边界值不触发修正")


def test_check_plan_edge_pagesize_1001():
    """测试 8：边界值测试 - pagesize = 1001 触发修正"""
    registry = get_skill_registry()

    # pagesize 刚好 1001（刚越界）
    plan = [
        {"task": "ai_event_list", "args": {"pagesize": 1001}, "status": "pending"}
    ]

    result = check_plan(plan, registry)

    # 验证：应放行但修正
    assert result.allow, "pagesize=1001 应放行"
    assert result.fixed_plan is not None, "pagesize=1001 应触发修正"
    assert result.fixed_plan[0]["args"]["pagesize"] == 100, "应被修正为 100"
    print("✅ 测试 8 通过：pagesize=1001 边界值触发修正")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("H6 单元测试：check_plan 三类校验")
    print("=" * 60)

    tests = [
        ("工具存在性校验", test_check_plan_invalid_tool),
        ("event_type 合法性校验", test_check_plan_invalid_event_type),
        ("pagesize 越界自动修正", test_check_plan_pagesize_overflow),
        ("合法 plan 放行", test_check_plan_valid_plan),
        ("特殊工具放行", test_check_plan_special_tools),
        ("多步骤非法拦截", test_check_plan_multi_step),
        ("边界值 pagesize=1000", test_check_plan_edge_pagesize),
        ("边界值 pagesize=1001", test_check_plan_edge_pagesize_1001),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"\n[{name}]")
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ 测试失败: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ 测试异常: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果：✅ {passed} 通过，❌ {failed} 失败")
    print("=" * 60)

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)
