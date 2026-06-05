"""
测试 Executor 步骤间传参功能

验证：
1. 模板语法 {{step_N.field}} 能正确解析
2. 支持嵌套字段访问 {{step_0.data.camera_id}}
3. 引用不存在的步骤/字段时优雅降级
"""
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.nodes import _resolve_step_references, _get_nested_field


def test_simple_field_reference():
    """测试简单字段引用"""
    args = {
        "camera_id": "{{step_0.camera_id}}",
        "date": "2026-06-01"
    }
    step_outputs = {
        0: {"camera_id": "CAM-005", "ts_event": 1234567890}
    }

    resolved = _resolve_step_references(args, step_outputs, current_idx=1)

    assert resolved["camera_id"] == "CAM-005", f"Expected 'CAM-005', got {resolved['camera_id']}"
    assert resolved["date"] == "2026-06-01"
    print("✅ 简单字段引用测试通过")


def test_nested_field_reference():
    """测试嵌套字段访问"""
    args = {
        "video_path": "{{step_0.result.video_url}}"
    }
    step_outputs = {
        0: {"result": {"video_url": "/video/CAM-005/clip.mp4", "duration": 20}}
    }

    resolved = _resolve_step_references(args, step_outputs, current_idx=1)

    assert resolved["video_path"] == "/video/CAM-005/clip.mp4"
    print("✅ 嵌套字段访问测试通过")


def test_multiple_references_in_one_string():
    """测试同一字符串中的多个引用"""
    args = {
        "message": "告警来自 {{step_0.camera_id}}，时间戳 {{step_0.ts_event}}"
    }
    step_outputs = {
        0: {"camera_id": "CAM-005", "ts_event": 1234567890}
    }

    resolved = _resolve_step_references(args, step_outputs, current_idx=1)

    assert resolved["message"] == "告警来自 CAM-005，时间戳 1234567890"
    print("✅ 多引用拼接测试通过")


def test_missing_step_graceful_degradation():
    """测试引用不存在的步骤时不崩溃"""
    args = {
        "camera_id": "{{step_5.camera_id}}"  # 步骤 5 不存在
    }
    step_outputs = {
        0: {"camera_id": "CAM-005"}
    }

    resolved = _resolve_step_references(args, step_outputs, current_idx=1)

    # 引用失败时保持原样
    assert resolved["camera_id"] == "{{step_5.camera_id}}"
    print("✅ 缺失步骤优雅降级测试通过")


def test_missing_field_graceful_degradation():
    """测试引用不存在的字段时不崩溃"""
    args = {
        "camera_id": "{{step_0.non_existent_field}}"
    }
