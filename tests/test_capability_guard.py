"""能力边界护栏单元测试（2026-07-09）

覆盖两类"非法诉求裹在合理诉求里"的场景：
  1. check_future_record：查未来时刻录像 → 硬拦截（present/past 锚点必放行）
  2. content_retrieval_notice：按画面内容检索录像 → 软降级说明
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.capability_guard import (
    check_future_record,
    content_retrieval_notice,
    is_capability_guard_enabled,
)


# ==================== check_future_record ====================

def test_future_record_blocked():
    """明确未来词 + 录像意图 → 拦截"""
    assert check_future_record("查看门口明天9点的录像") is not None
    assert check_future_record("播放门口下周一的录像") is not None
    assert check_future_record("调后天的录像") is not None


def test_present_past_record_pass():
    """present/past 锚点存在 → 一律放行（保护已验证可用场景）"""
    assert check_future_record("查看门口今天9点后有人出现的录像") is None
    assert check_future_record("查看门口昨天15点的录像") is None
    assert check_future_record("查看门口今天9点后天黑前的录像") is None
    assert check_future_record("播放门口现在的录像") is None


def test_future_nonrecord_pass():
    """非录像意图（如告警）不受未来护栏约束（各有其守卫）"""
    assert check_future_record("查询明天的告警") is None
    assert check_future_record("统计下周的数据") is None


# ==================== content_retrieval_notice ====================

def test_content_notice_triggered():
    """录像意图 + 内容检索标记 → 追加说明"""
    assert content_retrieval_notice("查看门口今天9点后有人出现的录像") is not None
    assert content_retrieval_notice("调门口今天有车的录像") is not None
    assert content_retrieval_notice("查门口抽烟的人的录像") is not None


def test_content_notice_not_triggered():
    """普通录像（无内容标记）→ 无说明"""
    assert content_retrieval_notice("查看门口今天9点的录像") is None
    assert content_retrieval_notice("播放181测试今天0点的录像") is None
    # 非录像意图不触发
    assert content_retrieval_notice("查询有人离岗的告警") is None


def test_guard_toggle():
    """总开关读取"""
    assert is_capability_guard_enabled({"harness": {"capability_guard_enabled": True}}) is True
    assert is_capability_guard_enabled({"harness": {"capability_guard_enabled": False}}) is False
    assert is_capability_guard_enabled({}) is True  # 默认启用


if __name__ == "__main__":
    tests = [
        test_future_record_blocked,
        test_present_past_record_pass,
        test_future_nonrecord_pass,
        test_content_notice_triggered,
        test_content_notice_not_triggered,
        test_guard_toggle,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)
