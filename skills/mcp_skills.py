"""动态注册 MCP Tools 为 Skill"""
from loguru import logger

from skills.base import Skill, SkillType
from skills.registry import SkillRegistry


def _classify_tool(tool_name: str) -> list[str]:
    """根据工具名前缀打 tag，便于 Web/Planner 分组展示"""
    tags = ["mcp", "platform"]
    if tool_name.startswith("ai_"):
        tags.extend(["ai_event", "alarm"])
    elif tool_name.startswith("video_"):
        tags.append("video")
    elif tool_name.startswith("system_"):
        tags.append("system")
    return tags


async def register_mcp_skills(registry: SkillRegistry, mcp_client) -> None:
    """动态注册 MCP Server 暴露的工具为 Skill

    Args:
        registry: Skill Registry
        mcp_client: MCP Client 实例（已连接）
    """
    logger.info("开始注册 MCP Skills...")

    total = 0
    for server_name in mcp_client.list_servers():
        try:
            tools = await mcp_client.list_tools(server_name)
            logger.info(f"发现 {len(tools)} 个 MCP 工具 from {server_name}")

            for tool in tools:
                skill = Skill(
                    id=tool["name"],
                    name=tool["name"],
                    description=tool["description"],
                    parameters=tool["inputSchema"],
                    implementation=None,  # 通过 MCP 调用
                    skill_type=SkillType.MCP_TOOL,
                    mcp_server=server_name,
                    tags=_classify_tool(tool["name"]) + [server_name],
                )
                registry.register(skill)
                total += 1

        except Exception as e:
            logger.error(f"注册 MCP server {server_name} 失败: {e}")

    logger.info(f"MCP Skills 注册完成，共 {total} 个工具")
