"""Skill Registry - 统一工具注册表"""
import asyncio
from typing import Any
from loguru import logger

from skills.base import Skill, SkillType


class SkillRegistry:
    """Skill 注册表

    管理所有可用的 Skill（MCP 工具、本地工具、子图），
    提供统一的注册、查询和调用接口。
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._mcp_client = None

    def set_mcp_client(self, mcp_client):
        """设置 MCP Client（用于调用 MCP 工具）"""
        self._mcp_client = mcp_client

    def register(self, skill: Skill) -> None:
        """注册 Skill"""
        if skill.id in self._skills:
            logger.warning(f"Skill {skill.id} 已存在，将被覆盖")
        self._skills[skill.id] = skill
        logger.info(f"注册 Skill: {skill.id} ({skill.skill_type.value})")

    def unregister(self, skill_id: str) -> None:
        """注销 Skill"""
        if skill_id in self._skills:
            del self._skills[skill_id]
            logger.info(f"注销 Skill: {skill_id}")

    def get(self, skill_id: str) -> Skill | None:
        """获取 Skill"""
        return self._skills.get(skill_id)

    def list_skills(self, tags: list[str] | None = None) -> list[Skill]:
        """列出可用 Skill（可按 tag 过滤）"""
        skills = list(self._skills.values())

        if tags:
            skills = [
                s for s in skills
                if any(tag in (s.tags or []) for tag in tags)
            ]

        return skills

    async def invoke(self, skill_id: str, args: dict, context: dict | None = None) -> dict:
        """调用 Skill（统一入口）

        Args:
            skill_id: Skill ID
            args: 参数字典
            context: 上下文（session_id, trace_id 等）

        Returns:
            结果字典，包含 error 字段（如果失败）
        """
        skill = self.get(skill_id)
        if not skill:
            return {"error": f"unknown skill: {skill_id}"}

        logger.info(f"调用 Skill: {skill_id} ({skill.skill_type.value})")

        try:
            if skill.skill_type == SkillType.MCP_TOOL:
                # 通过 MCP Client 调用
                # 每次都重新获取 client，确保在当前线程/事件循环中可用
                from mcp_adapter.client import get_mcp_client
                mcp_client = await get_mcp_client()

                if not mcp_client or not mcp_client.enabled:
                    return {"error": "MCP client not initialized"}

                # 检查连接是否有效
                if skill.mcp_server not in mcp_client.servers:
                    logger.warning(f"MCP server {skill.mcp_server} 未连接，尝试重连...")
                    try:
                        mcp_client = await get_mcp_client(force_reconnect=True)
                        if skill.mcp_server not in mcp_client.servers:
                            return {"error": f"MCP server {skill.mcp_server} 重连失败"}
                        logger.info(f"MCP server {skill.mcp_server} 重连成功")
                    except Exception as e:
                        logger.error(f"重连 MCP server 失败: {e}")
                        return {"error": f"MCP server reconnect failed: {e}"}

                result = await mcp_client.call_tool(
                    server_name=skill.mcp_server,
                    tool_name=skill_id,
                    args=args
                )
                return result

            elif skill.skill_type == SkillType.TOOL:
                # 本地函数调用
                if asyncio.iscoroutinefunction(skill.implementation):
                    result = await skill.implementation(args, context or {})
                else:
                    result = skill.implementation(args, context or {})
                return result

            elif skill.skill_type == SkillType.SUBGRAPH:
                # 子图调用：实现可以是 async 函数或可调用的编译图
                if skill.implementation is None:
                    return {"error": f"subgraph {skill_id} has no implementation"}
                if asyncio.iscoroutinefunction(skill.implementation):
                    result = await skill.implementation(args, context or {})
                else:
                    result = skill.implementation(args, context or {})
                return result

            else:
                return {"error": f"unsupported skill type: {skill.skill_type}"}

        except Exception as e:
            logger.exception(f"Skill {skill_id} 调用失败")
            return {"error": f"{type(e).__name__}: {e}"}


# 全局单例
_registry = None


def get_skill_registry() -> SkillRegistry:
    """获取全局 Skill Registry"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
