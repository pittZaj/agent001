"""Skill 基础定义"""
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Any


class SkillType(Enum):
    """Skill 类型"""
    TOOL = "tool"           # 本地 Python 函数
    MCP_TOOL = "mcp_tool"   # MCP 工具
    SUBGRAPH = "subgraph"   # LangGraph 子图


@dataclass
class Skill:
    """Skill 元数据

    每个 Skill 包含：
    - id: 唯一标识（如 "query_alarms"）
    - name: 显示名称
    - description: 功能描述（给 Planner 看）
    - parameters: JSON Schema（参数定义）
    - implementation: 实现函数或子图
    - skill_type: SkillType 枚举
    - mcp_server: 如果是 mcp_tool，指定 server 名称
    - tags: 标签列表（用于分类和过滤）
    """
    id: str
    name: str
    description: str
    parameters: dict
    implementation: Callable | None
    skill_type: SkillType
    mcp_server: str | None = None
    tags: list[str] | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

        # 校验：mcp_tool 必须指定 mcp_server
        if self.skill_type == SkillType.MCP_TOOL and not self.mcp_server:
            raise ValueError(f"Skill {self.id}: mcp_tool 必须指定 mcp_server")

        # 校验：非 mcp_tool 必须有 implementation
        if self.skill_type != SkillType.MCP_TOOL and self.implementation is None:
            raise ValueError(f"Skill {self.id}: {self.skill_type.value} 必须提供 implementation")
