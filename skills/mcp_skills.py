"""动态注册 MCP Tools 为 Skill"""
from loguru import logger

from skills.base import Skill, SkillType
from skills.registry import SkillRegistry


async def register_mcp_skills(registry: SkillRegistry, mcp_client) -> None:
    """动态注册 MCP Server 暴露的工具为 Skill

    Args:
        registry: Skill Registry
        mcp_client: MCP Client 实例
    """
    logger.info("开始注册 MCP Skills...")

    # 获取所有已连接的 server
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
                    implementation=None,  # 通过 MCP 调用，不需要本地实现
                    skill_type=SkillType.MCP_TOOL,
                    mcp_server=server_name,
                    tags=["data", "mcp", server_name]
                )
                registry.register(skill)

        except Exception as e:
            logger.error(f"注册 MCP server {server_name} 失败: {e}")

    logger.info("MCP Skills 注册完成")
