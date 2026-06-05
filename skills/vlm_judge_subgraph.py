"""大模型复判子图（SUBGRAPH 类型 Skill）。

将 VLM 多模态复判封装为可被 Planner 编排的 Skill。
支持全部 8 类告警（由 alarm_types 表驱动 display_name）。

输入：
    - alarm_uuid: 告警 UUID（从库读 snapshot_url 和 alarm_type）
    - 或 image_path + alarm_type: 直接指定图片和类型
输出：
    {"verdict": "confirmed"|"rejected"|"uncertain",
     "exists": bool, "confidence": float, "reasoning": str,
     "alarm_uuid": ..., "alarm_type": ..., "display_name": ...}
"""
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from skills.base import Skill, SkillType
from utils.vlm import get_vlm_client

# 数据库路径（与 MCP server 解析逻辑一致）
DB_PATH = Path(__file__).resolve().parent.parent / "agent" / "data" / "ksipms_dev.db"


def _lookup_alarm(alarm_uuid: str) -> dict | None:
    """从库读取告警的 snapshot_url 和 alarm_type"""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT alarm_uuid, alarm_type, snapshot_url, model_conf FROM alarms WHERE alarm_uuid=?",
            (alarm_uuid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _lookup_display_name(alarm_type: str) -> str:
    """从 alarm_types 表读 display_name"""
    if not DB_PATH.exists():
        return alarm_type
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT display_name FROM alarm_types WHERE type_code=?", (alarm_type,)
        ).fetchone()
        return row[0] if row else alarm_type
    finally:
        conn.close()


async def vlm_judge_impl(args: dict, context: dict) -> dict:
    """复判 Skill 实现"""
    alarm_uuid = args.get("alarm_uuid")
    image_path = args.get("image_path")
    alarm_type = args.get("alarm_type")
    model_conf = args.get("model_conf")

    # 路径1：传入 alarm_uuid，从库解析图片和类型
    if alarm_uuid:
        alarm = _lookup_alarm(alarm_uuid)
        if not alarm:
            return {"error": f"告警不存在: {alarm_uuid}"}
        image_path = alarm["snapshot_url"]
        alarm_type = alarm["alarm_type"]
        model_conf = alarm.get("model_conf")

    if not image_path:
        return {"error": "缺少 image_path 或 alarm_uuid"}
    if not alarm_type:
        return {"error": "缺少 alarm_type"}

    # 检查图片是否为本地路径且存在
    if image_path.startswith("/") and not Path(image_path).exists():
        return {"error": f"图片文件不存在: {image_path}"}
    # snapshot_url 若是 http(s)（业务模拟数据），无法读取，返回提示
    if image_path.startswith("http"):
        return {"error": f"快照为远程 URL，无法本地复判: {image_path}（请使用测试数据集图片）"}

    display_name = _lookup_display_name(alarm_type)
    logger.info(f"[VLMJudge] 复判 alarm_uuid={alarm_uuid} type={alarm_type}({display_name})")

    try:
        vlm = get_vlm_client()
        result = vlm.judge_alarm_type(
            display_name=display_name,
            image_path=image_path,
            model_conf=model_conf,
        )
        result.update({
            "alarm_uuid": alarm_uuid,
            "alarm_type": alarm_type,
            "display_name": display_name,
            "error": None,
        })
        return result
    except Exception as e:
        logger.exception("[VLMJudge] 复判失败")
        return {"error": f"VLM 复判失败: {type(e).__name__}: {e}"}


def register_vlm_judge_skill(registry):
    """注册复判子图 Skill"""
    registry.register(Skill(
        id="vlm_judge_alarm",
        name="大模型复判告警",
        description="读取告警快照图片，用多模态大模型复判该告警是否真实（支持全部8类告警）。"
                    "传入 alarm_uuid 自动从库读取图片和类型。返回 verdict(confirmed/rejected/uncertain)、confidence、reasoning。",
        parameters={
            "type": "object",
            "properties": {
                "alarm_uuid": {"type": "string", "description": "告警UUID（推荐，自动读取图片和类型）"},
                "image_path": {"type": "string", "description": "图片本地路径（不传 alarm_uuid 时使用）"},
                "alarm_type": {"type": "string", "description": "告警类型 type_code（配合 image_path 使用）"},
            },
        },
        implementation=vlm_judge_impl,
        skill_type=SkillType.SUBGRAPH,
        tags=["vlm", "judge", "multimodal"],
    ))
    logger.info("复判子图 Skill 已注册: vlm_judge_alarm")
