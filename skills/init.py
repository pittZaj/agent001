"""初始化 Skill Registry，注册所有可用的 Skills（简化版：直接注册工具函数）"""
import asyncio
from loguru import logger

from skills import get_skill_registry, Skill, SkillType


def register_local_skills(registry):
    """注册本地 Skill（非 MCP）"""

    # direct_response: 直接回复
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

    logger.info("本地 Skills 注册完成")


def register_mcp_tools_as_local(registry):
    """将 MCP 工具实现注册为本地 Skill（阶段 2 临时方案）"""
    from mcp_servers.ksipms_server import query_alarms_impl, query_person_impl, query_video_impl

    # query_alarms
    def query_alarms_wrapper(args: dict, context: dict) -> dict:
        return query_alarms_impl(**args)

    registry.register(Skill(
        id="query_alarms",
        name="查询告警记录",
        description="查询告警记录，支持按日期、类型、摄像头筛选。日期格式：YYYY-MM-DD（UTC），不填则查最近7天",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD（UTC），留空则查最近7天"},
                "alarm_type": {"type": "string", "description": "告警类型（可选）：smoking/no_helmet/phone_use/no_mask"},
                "camera_id": {"type": "string", "description": "摄像机ID（可选）"}
            }
        },
        implementation=query_alarms_wrapper,
        skill_type=SkillType.TOOL,
        tags=["data", "alarm"]
    ))

    # query_person
    def query_person_wrapper(args: dict, context: dict) -> dict:
        return query_person_impl(**args)

    registry.register(Skill(
        id="query_person",
        name="查询人员信息",
        description="查询人员信息及最近7天告警统计",
        parameters={
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "人员ID"}
            },
            "required": ["person_id"]
        },
        implementation=query_person_wrapper,
        skill_type=SkillType.TOOL,
        tags=["data", "person"]
    ))

    # query_video
    def query_video_wrapper(args: dict, context: dict) -> dict:
        return query_video_impl(**args)

    registry.register(Skill(
        id="query_video",
        name="查询录像片段",
        description="查询录像片段，需要摄像机ID和时间范围",
        parameters={
            "type": "object",
            "properties": {
                "camera_id": {"type": "string", "description": "摄像机ID"},
                "start_time": {"description": "开始时间（epoch秒 或 ISO格式）"},
                "end_time": {"description": "结束时间（epoch秒 或 ISO格式）"}
            },
            "required": ["camera_id", "start_time", "end_time"]
        },
        implementation=query_video_wrapper,
        skill_type=SkillType.TOOL,
        tags=["data", "video"]
    ))

    logger.info("MCP 工具已注册为本地 Skill（临时方案）")


async def init_skill_registry():
    """初始化 Skill Registry（简化版：不通过 MCP 协议）

    阶段 2 临时方案：
    - 直接调用 MCP Server 的工具实现函数
    - 不通过 stdio 协议
    - 后续可切换回完整 MCP 协议
    """
    logger.info("初始化 Skill Registry...")

    registry = get_skill_registry()

    # 注册本地 Skills
    register_local_skills(registry)

    # 注册 MCP 工具（直接调用实现）
    register_mcp_tools_as_local(registry)

    # 注册复判子图（多模态 VLM 复判，支持 8 类告警）
    from skills.vlm_judge_subgraph import register_vlm_judge_skill
    register_vlm_judge_skill(registry)

    # 注册告警业务 Skills（聚合统计 / 可视化 / 录像回溯 / 状态回写）
    from skills.alarm_skills import register_alarm_skills
    register_alarm_skills(registry)

    # 注册知识库检索 Skill（阶段3 RAG，规章制度联动）
    from skills.kb.skill import register_kb_skill
    register_kb_skill(registry)

    logger.info(f"Skill Registry 初始化完成，共注册 {len(registry.list_skills())} 个 Skill")

    return registry
