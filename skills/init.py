"""初始化 Skill Registry：连接真实 MCP Server 并动态注册 19 个工具

阶段 2.5.1 改造：
- 删除 register_mcp_tools_as_local（直接 import 本地实现的临时方案）
- 通过 mcp_adapter 连接 192.168.1.199:6620/mcp，动态注册 19 个 MCP 工具
- 保留本地 Skills（direct_response）、子图（vlm_judge_alarm / kb_regulation）
- 告警业务 Skills（aggregate_alarms / visualize_alarms / update_alarm_status / fetch_alarm_context）
  改为消费 MCP 工具输出（见 skills/alarm_skills.py）
"""
from loguru import logger

from skills import get_skill_registry, Skill, SkillType


def register_local_skills(registry):
    """注册本地基础 Skill（非 MCP，非业务）"""

    def direct_response_impl(args: dict, context: dict) -> dict:
        return {"text": args.get("text", ""), "error": None}

    registry.register(Skill(
        id="direct_response",
        name="直接回复",
        description="直接返回文本内容，不需要调用外部工具",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "回复内容"}
            },
            "required": ["text"]
        },
        implementation=direct_response_impl,
        skill_type=SkillType.TOOL,
        tags=["basic"]
    ))

    logger.info("本地基础 Skills 注册完成")


async def init_skill_registry():
    """初始化 Skill Registry（阶段 2.5.1：真实平台对接）

    注册顺序：
        1. 本地基础 Skills（direct_response）
        2. MCP Server 连接 + 19 个真实工具动态注册
        3. 复判子图（VLM）
        4. 告警业务 Skills（聚合/可视化/回写，消费 MCP 输出）
        5. 知识库子图（RAG）
    """
    logger.info("初始化 Skill Registry（真实平台对接）...")

    registry = get_skill_registry()

    # 1. 本地基础 Skills
    register_local_skills(registry)

    # 2. 连接真实 MCP Server，动态注册 19 个工具
    from mcp_adapter.client import get_mcp_client
    mcp_client = await get_mcp_client()
    registry.set_mcp_client(mcp_client)

    if mcp_client.enabled and mcp_client.list_servers():
        from skills.mcp_skills import register_mcp_skills
        await register_mcp_skills(registry, mcp_client)
    else:
        logger.warning("MCP Client 未启用或未连接，跳过 MCP 工具注册")

    # 3. 复判子图（VLM 多模态告警复判）
    from skills.vlm_judge_subgraph import register_vlm_judge_skill
    register_vlm_judge_skill(registry)

    # 4. 告警业务 Skills（已改造为消费 MCP 输出）
    from skills.alarm_skills import register_alarm_skills
    register_alarm_skills(registry)

    # 5. 知识库检索 Skill（RAG，与平台对接独立）
    from skills.kb.skill import register_kb_skill
    register_kb_skill(registry)

    skills = registry.list_skills()
    by_type: dict[str, int] = {}
    for s in skills:
        by_type[s.skill_type.value] = by_type.get(s.skill_type.value, 0) + 1
    logger.info(
        f"Skill Registry 初始化完成，共 {len(skills)} 个 Skill "
        f"(分布: {by_type})"
    )

    return registry
