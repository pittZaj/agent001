"""端到端测试：用户询问 → Plan → Skill Registry → MCP Server → 返回结果"""
import asyncio
import sys
from pathlib import Path

# 添加 agent 目录到路径
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from loguru import logger
from skills.init import init_skill_registry
from graph import get_graph


async def test_e2e():
    """端到端测试"""
    logger.info("=" * 60)
    logger.info("开始端到端测试")
    logger.info("=" * 60)

    # 1. 初始化 Skill Registry
    logger.info("\n[步骤 1] 初始化 Skill Registry")
    registry = await init_skill_registry()

    # 列出所有已注册的 Skill
    skills = registry.list_skills()
    logger.info(f"已注册 {len(skills)} 个 Skill:")
    for skill in skills:
        logger.info(f"  - {skill.id} ({skill.skill_type.value}): {skill.description}")

    # 2. 测试直接调用 MCP Skill
    logger.info("\n[步骤 2] 测试直接调用 MCP Skill: query_alarms")
    result = await registry.invoke("query_alarms", {"date": "2026-06-01"}, {})
    logger.info(f"调用结果: {result}")

    # 3. 测试完整的 Plan-Execute 流程
    logger.info("\n[步骤 3] 测试完整的 Plan-Execute 流程")
    graph = get_graph()

    initial_state = {
        "session_id": "test_session",
        "user_message": "今天发生了哪些告警？",
        "plan": [],
        "current_task_idx": 0,
        "tool_results": [],
        "final_response": "",
        "error": None,
        "messages": [],
    }

    logger.info(f"用户消息: {initial_state['user_message']}")

    try:
        final_state = graph.invoke(initial_state)

        logger.info("\n[执行结果]")
        logger.info(f"生成的计划: {final_state.get('plan', [])}")
        logger.info(f"工具调用结果: {final_state.get('tool_results', [])}")
        logger.info(f"最终响应: {final_state.get('final_response', '')}")

        if final_state.get("error"):
            logger.error(f"错误: {final_state['error']}")
        else:
            logger.success("✅ 端到端测试通过！")

    except Exception as e:
        logger.exception(f"❌ 测试失败: {e}")
        return False

    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    asyncio.run(test_e2e())
