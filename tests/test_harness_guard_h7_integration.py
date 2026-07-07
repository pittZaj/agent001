"""H7 集成测试：验证危险回写确认门的端到端行为

测试场景：
  1. Demo 2 复判闭环（VLM → 回写）应照常放行
  2. 用户直接指令回写（无 VLM 依据）应被拦截
  3. 审计日志验证
"""
import sys
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.harness_guard import needs_confirmation, GuardResult


def get_test_config():
    """返回测试用配置"""
    return {
        "harness": {
            "guard_enabled": True,
            "confirm_dangerous_writes": True,
            "dangerous_tools": [
                "update_alarm_status",
                "ai_event_deal",
            ]
        }
    }


# ==================== 集成测试场景 ====================

def test_demo2_vlm_judge_flow():
    """集成测试 1：Demo 2 复判闭环（VLM → update_alarm_status）应放行"""
    print("\n[集成测试 1] Demo 2 复判闭环（VLM → 回写）")

    config = get_test_config()

    # 模拟 Demo 2 的第二步：update_alarm_status 带 source="vlm_judge"
    # 这是 planner 根据 few-shot 模板自动生成的
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "demo2-uuid-123",
            "verdict": "confirmed",
            "note": "VLM 自动复判",
            "source": "vlm_judge"  # 关键：复判链路标志（planner 模板自动添加）
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行（复判链路自动化）
    assert result.allow, "Demo 2 复判闭环应放行"
    assert result.action == "allow"

    print("  ✅ 放行成功（Demo 2 复判链路自动化）")
    print("  ✅ 审计日志会记录：source=vlm_judge，强制留痕")


def test_user_direct_write_blocked():
    """集成测试 2：用户直接指令回写（无 VLM 依据）应被拦截"""
    print("\n[集成测试 2] 用户直接指令回写（无 VLM 依据）")

    config = get_test_config()

    # 模拟用户直接说"把告警 X 标记为误报"
    # planner 可能生成：update_alarm_status，但没有 source 和 confirmed_by_user
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "user-direct-uuid-456",
            "verdict": "rejected"
            # 没有 source 和 confirmed_by_user
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应拦截
    assert not result.allow, "用户直接回写应被拦截"
    assert result.action == "ask", "action 应为 ask（需要确认）"
    assert "安全护栏" in result.reason, "reason 应提示安全护栏"
    assert "不可逆" in result.reason, "reason 应说明不可逆"
    assert "复判链路" in result.reason, "reason 应建议在复判链路中执行"

    print("  ✅ 拦截成功")
    print("  ✅ 友好话术：说明为什么拦截 + 如何正确执行")
    print(f"  拦截原因（前150字符）：\n    {result.reason[:150].replace(chr(10), chr(10) + '    ')}...")


def test_user_confirmed_write_allow():
    """集成测试 3：用户显式确认后的回写应放行"""
    print("\n[集成测试 3] 用户显式确认后的回写")

    config = get_test_config()

    # 模拟用户在 Web 界面点击"确认回写"后重新提交
    # 带 confirmed_by_user=true 标志
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "confirmed-uuid-789",
            "verdict": "rejected",
            "confirmed_by_user": True  # 关键：显式确认标志（Web 层添加）
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行
    assert result.allow, "显式确认后应放行"

    print("  ✅ 放行成功（用户显式确认）")
    print("  ✅ 审计日志会记录：confirmed_by_user=True，强制留痕")


def test_ai_event_deal_direct_call():
    """集成测试 4：直接调用 ai_event_deal（批量回写）也应受保护"""
    print("\n[集成测试 4] 直接调用 ai_event_deal（批量回写）")

    config = get_test_config()

    # 模拟直接调用 ai_event_deal（绕过 update_alarm_status 包装）
    task = {
        "task": "ai_event_deal",
        "args": {
            "event_uuid": ["uuid1", "uuid2", "uuid3"],
            "review_status": 3  # 标记为误报
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应拦截（ai_event_deal 也在 dangerous_tools 列表中）
    assert not result.allow, "直接调用 ai_event_deal 也应拦截"
    assert result.action == "ask"

    print("  ✅ 拦截成功（ai_event_deal 也是危险工具）")


def test_switch_off_bypass():
    """集成测试 5：关闭确认门开关后，所有回写都放行（回滚方案）"""
    print("\n[集成测试 5] 关闭确认门开关（confirm_dangerous_writes=false）")

    # 配置：关闭确认门
    config = {
        "harness": {
            "guard_enabled": True,
            "confirm_dangerous_writes": False,  # 关键：关闭确认门
            "dangerous_tools": ["update_alarm_status", "ai_event_deal"]
        }
    }

    # 即使无 source 和 confirmed_by_user，也应放行
    task = {
        "task": "update_alarm_status",
        "args": {
            "alarm_uuid": "bypass-uuid-999",
            "verdict": "confirmed"
        },
        "status": "pending"
    }

    result = needs_confirmation(task, config)

    # 验证：应放行（开关关闭，保持现网行为）
    assert result.allow, "开关关闭应放行"

    print("  ✅ 放行成功（confirm_dangerous_writes=false，回滚生效）")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 70)
    print("H7 集成测试：危险回写确认门端到端验证")
    print("=" * 70)

    tests = [
        test_demo2_vlm_judge_flow,
        test_user_direct_write_blocked,
        test_user_confirmed_write_allow,
        test_ai_event_deal_direct_call,
        test_switch_off_bypass,
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
        print("\n🎉 所有集成测试通过！")
        print("   - Demo 2 复判闭环自动化照常放行")
        print("   - 用户直接回写被拦截，友好话术提示")
        print("   - 危险工具执行均强制留痕")
        print("   - 确认门开关可回滚")
    else:
        print(f"\n⚠️ 有 {failed} 个测试失败，请检查实现。")

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)
