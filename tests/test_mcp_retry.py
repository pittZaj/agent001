"""测试 MCP Client call_tool 的重试与超时分级（T5）

验证三类错误的处理：
1. 瞬时错误（TimeoutError/ConnectionError 等）→ 指数退避重试
2. 业务错（isError=True）→ 立即返回，不重试
3. 未知异常 → 立即返回，不重试

关键约束：mock 不依赖真实 MCP Server，纯单元测试。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_adapter.client import MCPClient, RETRIABLE_EXCEPTIONS


def _make_client_with_session(session_mock):
    """构造一个绑定了 mock session 的 MCPClient（不连真实平台）"""
    client = MCPClient()
    # 直接注入 mock session（绕过真实 connect_*）
    client.servers["ksipms"] = {
        "session": session_mock,
        "exit_stack": MagicMock(),
        "transport": "http",
        "url": "mock",
    }
    # 注入重试配置（与 config.yaml 默认对齐）
    client.config = {
        "enabled": True,
        "timeout_call": 5,
        "retry": {
            "max_attempts": 3,
            "backoff_base": 0.01,  # 单测里加速：10ms 基数
            "backoff_max": 0.05,
        },
    }
    return client


def _make_success_result(data):
    """构造一个成功的 MCP CallToolResult mock"""
    result = MagicMock()
    result.isError = False
    result.structuredContent = data
    result.content = []
    return result


def _make_error_result(err_text):
    """构造一个 isError=True 的 MCP 业务错结果 mock"""
    result = MagicMock()
    result.isError = True
    content = MagicMock()
    content.text = err_text
    result.content = [content]
    return result


# ===================== 1. 瞬时错误：重试成功 =====================

def test_retry_transient_error_then_success():
    """瞬时错误（TimeoutError）前两次失败、第三次成功 → 最终成功"""
    session = MagicMock()
    # 前两次抛 TimeoutError，第三次返回成功
    session.call_tool = AsyncMock(
        side_effect=[
            asyncio.TimeoutError("simulated timeout 1"),
            asyncio.TimeoutError("simulated timeout 2"),
            _make_success_result({"events": [], "total": 0}),
        ]
    )

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "ai_event_list", {"pagesize": 5}))

    # 应返回成功结果（第三次的）
    assert result == {"events": [], "total": 0}, f"期望成功结果，实际：{result}"
    # 应被调用 3 次（前 2 次失败 + 第 3 次成功）
    assert session.call_tool.call_count == 3, f"期望调用 3 次，实际 {session.call_tool.call_count}"
    print("✅ 瞬时错误重试成功测试通过")


def test_retry_connection_error():
    """ConnectionError（可重试）：第一次失败，第二次成功"""
    session = MagicMock()
    session.call_tool = AsyncMock(
        side_effect=[
            ConnectionError("connection reset"),
            _make_success_result({"data": "ok"}),
        ]
    )

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "test_tool", {}))

    assert result == {"data": "ok"}, f"期望成功结果，实际：{result}"
    assert session.call_tool.call_count == 2
    print("✅ ConnectionError 重试测试通过")


# ===================== 2. 瞬时错误：耗尽重试次数 =====================

def test_retry_exhausted_returns_error():
    """瞬时错误持续发生 → 重试耗尽返回友好错误"""
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=asyncio.TimeoutError("always timeout"))

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "ai_event_list", {}))

    # 应返回 error 字段（不是 raise）
    assert "error" in result, f"期望返回 error 字段，实际：{result}"
    assert "重试 3 次仍失败" in result["error"], f"错误消息不含重试次数：{result['error']}"
    assert "TimeoutError" in result["error"], f"错误消息不含异常类型：{result['error']}"
    # 应被调用 3 次（max_attempts）
    assert session.call_tool.call_count == 3
    print("✅ 重试耗尽返回错误测试通过")


# ===================== 3. 业务错（isError=True）：不重试 =====================

def test_business_error_no_retry():
    """业务错（isError=True，如参数错）→ 立即返回，不重试"""
    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=_make_error_result("参数 event_uuid 缺失")
    )

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "ai_event_detail", {}))

    # 应立即返回业务错文本
    assert "error" in result
    assert result["error"] == "参数 event_uuid 缺失"
    # 关键：只调用 1 次，不重试
    assert session.call_tool.call_count == 1, f"业务错不应重试，实际调用 {session.call_tool.call_count} 次"
    print("✅ 业务错不重试测试通过")


# ===================== 4. 未知异常：不重试 =====================

def test_unknown_exception_no_retry():
    """非瞬时异常（如 ValueError）→ 立即返回，不重试"""
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=ValueError("unexpected error"))

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "test_tool", {}))

    assert "error" in result
    assert "ValueError" in result["error"]
    # 只调用 1 次，不重试
    assert session.call_tool.call_count == 1, f"非瞬时异常不应重试，实际 {session.call_tool.call_count} 次"
    print("✅ 未知异常不重试测试通过")


# ===================== 5. 正常路径：单次调用成功 =====================

def test_normal_path_single_call():
    """正常路径：第一次就成功 → 重试逻辑不触发"""
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_make_success_result({"x": 1}))

    client = _make_client_with_session(session)
    result = asyncio.run(client.call_tool("ksipms", "test_tool", {}))

    assert result == {"x": 1}
    # 关键：仅调用 1 次，重试逻辑零开销
    assert session.call_tool.call_count == 1
    print("✅ 正常路径单次调用测试通过")


# ===================== 6. server 未连接：立即返回 =====================

def test_server_not_connected():
    """server 不在 self.servers → 立即返回错误（不进入重试循环）"""
    client = _make_client_with_session(MagicMock())
    # 删除 servers，模拟未连接
    client.servers = {}

    result = asyncio.run(client.call_tool("ksipms", "test_tool", {}))

    assert "error" in result
    assert "未连接" in result["error"]
    print("✅ server 未连接立即返回测试通过")


# ===================== 7. 退避时间验证 =====================

def test_backoff_timing():
    """验证指数退避的时间（前两次失败后总等待 ≈ 0.01 + 0.02 = 0.03s）"""
    import time

    session = MagicMock()
    session.call_tool = AsyncMock(
        side_effect=[
            asyncio.TimeoutError("t1"),
            asyncio.TimeoutError("t2"),
            _make_success_result({"ok": True}),
        ]
    )

    client = _make_client_with_session(session)
    t0 = time.time()
    result = asyncio.run(client.call_tool("ksipms", "test_tool", {}))
    elapsed = time.time() - t0

    assert result == {"ok": True}
    # 前两次失败后等待：0.01 * 1 + 0.01 * 2 = 0.03s（指数退避）
    # 留宽容（最少 0.025s，最多 0.5s 容忍系统抖动）
    assert 0.025 < elapsed < 0.5, f"退避耗时异常：{elapsed:.3f}s"
    print(f"✅ 指数退避时间验证通过（耗时 {elapsed * 1000:.0f}ms）")


# ===================== 入口（直接运行 / pytest 都支持） =====================

if __name__ == "__main__":
    test_retry_transient_error_then_success()
    test_retry_connection_error()
    test_retry_exhausted_returns_error()
    test_business_error_no_retry()
    test_unknown_exception_no_retry()
    test_normal_path_single_call()
    test_server_not_connected()
    test_backoff_timing()
    print("\n🎉 所有 T5 单测通过！")
