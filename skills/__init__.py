"""Skill Registry - 统一工具注册表

提供统一的 Skill 抽象和注册机制，支持：
- MCP 工具（通过 MCP Client 调用）
- 本地工具（Python 函数）
- 子图（LangGraph Subgraph）
"""
from skills.base import Skill, SkillType
from skills.registry import SkillRegistry, get_skill_registry

__all__ = ["Skill", "SkillType", "SkillRegistry", "get_skill_registry"]
