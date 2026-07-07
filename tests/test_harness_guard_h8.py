"""任务 H8 单元测试：check_result 行动后统一核对

测试覆盖：
  1. isError 识别：{"error": ...} → deny
  2. ai_event_list 空结果：total=0 且 events=[] → deny
  3. aggregate_alarms 空结果：total=0 且 data=[] → deny
  4. 正常结果 → 放行
  5. 其他工具 → 放行
"""
import sys
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.harness_guard import check_result, GuardResult


def get_test_config():
    """返回测试用配置"""
    return {
        "harness": {
            "guard_enabled": True,
        }
    }


# ==================== 测试用例 ====================

def test_check_result_is_error():
    """测试 1：isError 识别 - 工具返回错误"""
    print("\n[测试 1] isError 识别：result 包含 error 字段")

    config = get_test_config()
    result = {"error": "MCP 连接失败"}

    guard_result = check_result("ai_event_list", result, config)

    # 验证：应拦截
    assert not guard_result.allow, "包含 error 应拦截"
    assert guard_result.action == "deny"
    assert "工具执行失败" in guard_result.reason
    assert "MCP 连接失败" in guard_result.reason

    print("  ✅ 拦截成功（isError 识别）")


def test_check_result_ai_event_list_empty():
    """测试 2：ai_event_list 空结果 - total=0 且 events=[]"""
    print("\n[测试 2] ai_event_list 空结果（total=0, events=[]）")

    config = get_test_config()
    result = {"total": 0, "events": []}

    guard_result = check_result("ai_event_list", result, config)

    # 验证：应拦截
    assert not guard_result.allow, "ai_event_list 空结果应拦截"
    assert guard_result.action == "deny"
    assert "返回空结果" in guard_result.reason

    print("  ✅ 拦截成功（ai_event_list 空结果）")


def test_check_result_aggregate_alarms_empty():
    """测试 3：aggregate_alarms 空结果 - total=0 且 data=[]"""
    print("\n[测试 3] aggregate_alarms 空结果（total=0, data=[]）")

    config = get_test_config()
    result = {"total": 0, "data": []}

    guard_result = check_result("aggregate_alarms", result, config)

    # 验证：应拦截
    assert not guard_result.allow, "aggregate_alarms 空结果应拦截"
    assert guard_result.action == "deny"
    assert "返回空结果" in guard_result.reason

    print("  ✅ 拦截成功（aggregate_alarms 空结果）")


def test_check_result_ai_event_list_normal():
    """测试 4：ai_event_list 正常结果 - 有数据"""
    print("\n[测试 4] ai_event_list 正常结果（有数据）")

    config = get_test_config()
    result = {
        "total": 5,
        "events": [
            {"uuid": "123", "event_name": "未戴安全帽"},
            {"uuid": "456", "event_name": "违规抽烟"},
        ]
    }

    guard_result = check_result("ai_event_list", result, config)

    # 验证：应放行
    assert guard_result.allow, "正常结果应放行"
    assert guard_result.action == "allow"

    print("  ✅ 放行成功（正常结果进入 LLM formatter）")


def test_check_result_other_tool():
    """测试 5：其他工具（未覆盖）→ 默认放行"""
    print("\n[测试 5] 其他工具（video_play_record）")

    config = get_test_config()
    result = {"url": "http://example.com/video.mp4", "success": True}

    guard_result = check_result("video_play_record", result, config)

    # 验证：应放行
    assert guard_result.allow, "未覆盖的工具应放行"

    print("  ✅ 放行成功（未覆盖的工具默认放行）")


def test_check_result_edge_total_nonzero_but_empty_list():
    """测试 6：边界情况 - total > 0 但 events=[]（数据不一致）"""
    print("\n[测试 6] 边界情况：total > 0 但 events=[]")

    config = get_test_config()
    result = {"total": 5, "events": []}  # 数据不一致（后端 bug）

    guard_result = check_result("ai_event_list", result, config)

    # 验证：应放行（total > 0 不算空结果）
    assert guard_result.allow, "total > 0 不算空结果"

    print("  ✅ 放行成功（total > 0 优先级更高）")


def test_check_result_edge_total_zero_but_has_events():
    """测试 7：边界情况 - total=0 但 events 非空（数据不一致）"""
    print("\n[测试 7] 边界情况：total=0 但 events 非空")

    config = get_test_config()
    result = {"total": 0, "events": [{"uuid": "123"}]}  # 数据不一致（后端 bug）

    guard_result = check_result("ai_event_list", result, config)

    # 验证：应放行（events 非空不算空结果）
    assert guard_result.allow, "events 非空不算空结果"

    print("  ✅ 放行成功（events 非空优先级更高）")


def test_check_result_aggregate_partial_empty():
    """测试 8：aggregate_alarms 部分空 - total=0 但 data 有值"""
    print("\n[测试 8] aggregate_alarms：total=0 但 data 非空")

    config = get_test_config()
    result = {"total": 0, "data": [{"event_name": "测试", "count": 0}]}

    guard_result = check_result("aggregate_alarms", result, config)

    # 验证：应放行（data 非空不算空结果）
    assert guard_result.allow, "data 非空不算空结果"

    print("  ✅ 放行成功（data 非空优先级更高）")


# ==================== 执行所有测试 ====================

if __name__ == "__main__":
    print("=" * 70)
    print("H8 单元测试：check_result 行动后统一核对")
    print("=" * 70)

    tests = [
        test_check_result_is_error,
        test_check_result_ai_event_list_empty,
        test_check_result_aggregate_alarms_empty,
        test_check_result_ai_event_list_normal,
        test_check_result_other_tool,
        test_check_result_edge_total_nonzero_but_empty_list,
        test_check_result_edge_total_zero_but_has_events,
        test_check_result_aggregate_partial_empty,
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
        print("\n🎉 所有单元测试通过！check_result 行动后统一核对工作正常。")
    else:
        print(f"\n⚠️ 有 {failed} 个测试失败，请检查实现。")

    # 退出码：0=全部通过，1=有失败
    sys.exit(0 if failed == 0 else 1)
