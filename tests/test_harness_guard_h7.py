"""任务 H7 单元测试：needs_confirmation 危险回写确认门

测试覆盖：
  1. 非危险工具 → 放行
  2. 危险工具 + source="vlm_judge" → 放行（复判链路自动化）
  3. 危险工具 + confirmed_by_user=true → 放行（显式确认）
  4. 危险工具 + 无依据无确认 → deny（需要确认）
  5. 确认门开关关闭 → 全部放行
"""
import sys
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.harness_guard import needs_confirmation, GuardResult


# ==================== Mock Config ====================

def get_test_config(confirm_enabled=True):
    """返回测试用配置"""
    return {
        "harness": {
            "guard_enabled": True,
            "confirm_dangerous_writes": confirm_enabled,
            "dangerous_tools": [
                "update_alarm_status",
                "ai_event_deal",
            ]
        }
    }


# ==================== 测试用例 ====================

def test_needs_confirmation_non_dangerous_tool():
    """测试 1：非危险工具 → 放行"""
    print("\n[测试 1] 非危险工具（如 ai_event_list）")

    config = get_test_config()
    task = {
        "task": "ai_event_list",
        "args": {"event_type": "ET03007"},
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行
    assert result.allow, "非危险工具应放行"
    assert result.action == "allow"

    print("  ✅ 放行成功（非危险工具无需确认）")


def test_needs_confirmation_vlm_judge_source():
    """测试 2：危险工具 + source="vlm_judge" → 放行（复判链路自动化）"""
    print("\n[测试 2] 危险工具 + source=vlm_judge（复判链路自动化）")

    config = get_test_config()
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "test-uuid-123",
            "verdict": "confirmed",
            "source": "vlm_judge"  # 关键：复判链路标志
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行（复判链路自动化）
    assert result.allow, "复判链路应放行"
    assert result.action == "allow"

    print("  ✅ 放行成功（复判链路自动化，强制留痕）")


def test_needs_confirmation_confirmed_by_user():
    """测试 3：危险工具 + confirmed_by_user=true → 放行（显式确认）"""
    print("\n[测试 3] 危险工具 + confirmed_by_user=true（显式确认）")

    config = get_test_config()
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "test-uuid-123",
            "verdict": "rejected",
            "confirmed_by_user": True  # 关键：显式确认标志
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行
    assert result.allow, "显式确认应放行"
    assert result.action == "allow"

    print("  ✅ 放行成功（用户显式确认）")


def test_needs_confirmation_no_source_no_confirm():
    """测试 4：危险工具 + 无依据无确认 → deny（需要确认）"""
    print("\n[测试 4] 危险工具 + 无依据无确认（用户直接回写）")

    config = get_test_config()
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "test-uuid-456",
            "verdict": "rejected"
            # 没有 source 和 confirmed_by_user
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应拦截
    assert not result.allow, "无依据无确认应拦截"
    assert result.action == "ask", "action 应为 ask"
    assert "安全护栏" in result.reason, "reason 应提示安全护栏"
    assert "test-uuid-456" in result.reason, "reason 应包含 UUID"
    assert "rejected" in result.reason, "reason 应包含 verdict"

    print("  ✅ 拦截成功")
    print(f"  拦截原因（前80字符）: {result.reason[:80]}...")


def test_needs_confirmation_switch_off():
    """测试 5：确认门开关关闭 → 全部放行"""
    print("\n[测试 5] 确认门开关关闭（confirm_dangerous_writes=false）")

    config = get_test_config(confirm_enabled=False)
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "test-uuid-789",
            "verdict": "confirmed"
            # 无 source 和 confirmed_by_user，但开关关闭
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行（开关关闭，保持现网行为）
    assert result.allow, "开关关闭应放行"

    print("  ✅ 放行成功（开关关闭，保持现网行为）")


def test_needs_confirmation_ai_event_deal():
    """测试 6：ai_event_deal（另一个危险工具）+ 无确认 → deny"""
    print("\n[测试 6] ai_event_deal（另一个危险工具）+ 无确认")

    config = get_test_config()
    task = {
        "task": "ai_event_deal",
        "args": {
            "event_uuid": ["uuid1", "uuid2"],
            "review_status": 2
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应拦截
    assert not result.allow, "ai_event_deal 无确认应拦截"
    assert result.action == "ask"

    print("  ✅ 拦截成功（ai_event_deal 也是危险工具）")


def test_needs_confirmation_edge_case_empty_args():
    """测试 7：边界情况 - args 为空"""
    print("\n[测试 7] 边界情况：args 为空")

    config = get_test_config()
    task = {
        "task": "update_alarm_status",
        "args": {},  # 空 args
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应拦截（无 source 和 confirmed_by_user）
    assert not result.allow, "空 args 应拦截"
    assert result.action == "ask"

    print("  ✅ 拦截成功（空 args 等价无确认）")


def test_needs_confirmation_multi_scenario():
    """测试 8：复合场景 - 同时带 source 和 confirmed_by_user"""
    print("\n[测试 8] 复合场景：同时带 source 和 confirmed_by_user")

    config = get_test_config()
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "test-uuid-999",
            "verdict": "confirmed",
            "source": "vlm_judge",
            "confirmed_by_user": True
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行（任一条件满足即可）
    assert result.allow, "复合场景应放行"

    print("  ✅ 放行成功（任一确认条件满足即可）")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 70)
    print("H7 单元测试：needs_confirmation 危险回写确认门")
    print("=" * 70)

    tests = [
        test_needs_confirmation_non_dangerous_tool,
        test_needs_confirmation_vlm_judge_source,
        test_needs_confirmation_confirmed_by_user,
        test_needs_confirmation_no_source_no_confirm,
        test_needs_confirmation_switch_off,
        test_needs_confirmation_ai_event_deal,
        test_needs_confirmation_edge_case_empty_args,
        test_needs_confirmation_multi_scenario,
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
    print(f"测试结果：✅ {passed} 通过，❌ {failed} 失败")
    print("=" * 70)

    if failed == 0:
        print("\n🎉 所有单元测试通过！危险回写确认门工作正常。")
    else:
        print(f"\n⚠️ 有 {failed} 个测试失败，请检查实现。")

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)
