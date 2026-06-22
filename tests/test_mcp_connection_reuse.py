"""测试 MCP 连接复用优化（任务 4.1）

验证点：
1. MCP 连接次数：一次请求只应看到 1 次"创建新连接"日志
2. 连接复用：后续工具调用应看到"复用已有连接"日志
3. 端到端耗时：多步任务的耗时应显著下降
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
from loguru import logger

# 设置日志级别为 DEBUG，以便看到 MCP 连接日志
logger.remove()
logger.add(lambda msg: print(msg, end=""), level="DEBUG")


async def test_multi_step_task():
    """测试多步任务的 MCP 连接复用"""
    from graph.graph import get_graph
    from graph.streaming import run_graph_stream

    print("\n" + "="*80)
    print("测试用例：查询未戴安全帽告警并统计类型（2 步任务）")
    print("="*80 + "\n")

    graph = get_graph()
    initial_state = {
        "user_message": "查询未戴安全帽的告警并统计类型",
        "messages": [],
    }

    t0 = time.time()
    mcp_connection_count = 0
    mcp_reuse_count = 0

    # 收集日志中的 MCP 连接信息
    events = []
    async for event in run_graph_stream(
        graph,
        initial_state,
        modality="text",
        thread_id="test-mcp-reuse"
    ):
        events.append(event)
        if event.get("event") == "done":
            break

    elapsed = time.time() - t0

    # 解析最终结果
    done_event = next((e for e in events if e.get("event") == "done"), None)
    if done_event:
        response = done_event["data"].get("response", "")
        plan = done_event["data"].get("plan", [])
        tool_calls = done_event["data"].get("tool_calls", [])

        print(f"\n✅ 任务完成")
        print(f"   耗时: {elapsed:.2f}s")
        print(f"   计划步数: {len(plan)}")
        print(f"   工具调用: {len(tool_calls)}")
        print(f"\n回答预览: {response[:200]}...")

        # 检查日志中的 MCP 连接信息（需要人工查看日志）
        print(f"\n💡 请检查日志：")
        print(f"   - 应看到 1 次 '[MCP] 创建新连接' 日志")
        print(f"   - 应看到 N 次 '[MCP] 复用已有连接' 日志（N >= 1）")
        print(f"   - 所有 MCP 调用应在同一线程 'ksagent-graph-stream'")
    else:
        print("❌ 任务失败")

    return elapsed


async def test_three_step_task():
    """测试 3 步任务的 MCP 连接复用"""
    from graph.graph import get_graph
    from graph.streaming import run_graph_stream

    print("\n" + "="*80)
    print("测试用例：统计每种告警类型数量并画柱状图（2-3 步任务）")
    print("="*80 + "\n")

    graph = get_graph()
    initial_state = {
        "user_message": "统计每种告警类型数量并画柱状图",
        "messages": [],
    }

    t0 = time.time()

    events = []
    async for event in run_graph_stream(
        graph,
        initial_state,
        modality="text",
        thread_id="test-mcp-reuse-2"
    ):
        events.append(event)
        if event.get("event") == "done":
            break

    elapsed = time.time() - t0

    done_event = next((e for e in events if e.get("event") == "done"), None)
    if done_event:
        plan = done_event["data"].get("plan", [])
        tool_calls = done_event["data"].get("tool_calls", [])

        print(f"\n✅ 任务完成")
        print(f"   耗时: {elapsed:.2f}s")
        print(f"   计划步数: {len(plan)}")
        print(f"   工具调用: {len(tool_calls)}")

    return elapsed


async def main():
    """运行所有测试"""
    print("\n" + "🚀 " * 20)
    print("MCP 连接复用优化验证（任务 4.1）")
    print("🚀 " * 20)

    # 测试 1：2 步任务
    elapsed1 = await test_multi_step_task()

    # 等待一下
    await asyncio.sleep(2)

    # 测试 2：3 步任务
    elapsed2 = await test_three_step_task()

    print("\n" + "="*80)
    print("📊 测试总结")
    print("="*80)
    print(f"测试 1 耗时: {elapsed1:.2f}s")
    print(f"测试 2 耗时: {elapsed2:.2f}s")
    print(f"\n✅ 优化验证建议：")
    print(f"   1. 检查日志中每个测试只有 1 次 '[MCP] 创建新连接'")
    print(f"   2. 检查日志中有多次 '[MCP] 复用已有连接'")
    print(f"   3. 检查无 'Attempted to exit cancel scope in a different task' 警告")
    print(f"   4. 与优化前的基线对比，耗时应下降 30%~60%")


if __name__ == "__main__":
    asyncio.run(main())
