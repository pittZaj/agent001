"""大模型复判子图（SUBGRAPH 类型 Skill）— 真实平台版。

输入：
    - alarm_uuid (推荐): AI 事件 UUID，自动调 ai_event_detail 读 img_path / event_type
    - 或 image_path/image_url + alarm_type: 直接指定图片（image_path 可以是本地路径或绝对 URL）

输出：
    {"verdict": "confirmed"|"rejected"|"uncertain",
     "exists": bool, "confidence": float, "reasoning": str,
     "alarm_uuid": ..., "alarm_type": ..., "display_name": ...}

字段对齐（与旧 SQLite 版差异）：
    snapshot_url(本地路径) -> img_path(相对路径，需拼 om_base_url 才能下载)
    alarm_type(自定义编码) -> event_type(平台编码 ET03007)
    alarm_name -> event_name (中文)
"""
import base64
from pathlib import Path

import requests
from loguru import logger

from skills.base import Skill, SkillType
from utils import CONFIG
from utils.vlm import get_vlm_client


def _om_base_url() -> str:
    """OM 静态资源根（用于拼接 img_path 相对路径）"""
    base = (CONFIG.get("mcp", {}) or {}).get("om_base_url", "").rstrip("/")
    return base or "http://192.168.1.199:6611"


def _resolve_image_url(img_path: str) -> str:
    """img_path 转为可下载的绝对 URL

    - 已是 http(s):// 直接返回
    - 相对路径（如 ai_data/event_report/...）拼 om_base_url
    - 本地绝对路径（以 / 开头且非 /ai_data）原样返回
    """
    if not img_path:
        return ""
    if img_path.startswith(("http://", "https://", "data:")):
        return img_path
    if img_path.startswith("/") and not img_path.startswith("/ai_data"):
        return img_path  # 本地文件路径
    rel = img_path.lstrip("/")
    return f"{_om_base_url()}/{rel}"


def _fetch_image_bytes(url_or_path: str, timeout: float = 15.0) -> bytes:
    """统一拉取图片字节（支持本地路径与 HTTP URL）"""
    if url_or_path.startswith(("http://", "https://")):
        resp = requests.get(url_or_path, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    # 本地文件
    p = Path(url_or_path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {url_or_path}")
    return p.read_bytes()


# 真实平台 event_type 编码 → 中文显示名（最常见的 8 类，未命中时退化用 event_name 字段）
_EVENT_TYPE_DISPLAY = {
    "ET03007": "未戴安全帽",
    "ET03001": "吸烟",
    "ET03002": "明火/烟雾",
    "ET03003": "未戴口罩",
    "ET03004": "接打电话",
    "ET03005": "区域入侵",
    "ET03006": "离岗",
    "ET03008": "睡岗",
}


async def vlm_judge_impl(args: dict, context: dict) -> dict:
    """复判 Skill 实现（真实平台版）

    路径优先级：
        1. 传 alarm_uuid: 调 ai_event_detail 拿 img_path / event_type
        2. 传 image_path / image_url + alarm_type
    """
    from skills import get_skill_registry

    alarm_uuid = args.get("alarm_uuid") or args.get("event_uuid")
    image_path = args.get("image_path") or args.get("image_url")
    alarm_type = args.get("alarm_type") or args.get("event_type")
    display_name = args.get("display_name") or args.get("event_name")

    # 路径 1：通过 MCP 拉详情
    if alarm_uuid and not image_path:
        registry = get_skill_registry()
        detail = await registry.invoke("ai_event_detail", {"event_uuid": alarm_uuid}, context)
        if detail.get("error"):
            return {"error": f"ai_event_detail 失败: {detail['error']}"}
        event = detail.get("event") or {}
        if not event:
            return {"error": f"事件不存在: {alarm_uuid}"}
        image_path = event.get("img_path")
        alarm_type = alarm_type or event.get("event_type")
        display_name = display_name or event.get("event_name")

    if not image_path:
        return {"error": "缺少 image_path / image_url 或 alarm_uuid"}

    # display_name 退化策略：event_name > _EVENT_TYPE_DISPLAY 映射 > event_type 原值
    if not display_name and alarm_type:
        display_name = _EVENT_TYPE_DISPLAY.get(alarm_type, alarm_type)
    if not display_name:
        return {"error": "无法确定告警类型显示名（缺少 alarm_type / event_name）"}

    # 拉图
    full_url = _resolve_image_url(image_path)
    logger.info(f"[VLMJudge] alarm_uuid={alarm_uuid} type={alarm_type}({display_name}) img={full_url}")
    try:
        image_bytes = _fetch_image_bytes(full_url)
    except Exception as e:
        logger.exception("[VLMJudge] 图片拉取失败")
        return {"error": f"图片拉取失败: {type(e).__name__}: {e}"}

    image_b64 = base64.b64encode(image_bytes).decode()

    # 调 VLM
    try:
        vlm = get_vlm_client()
        result = vlm.judge_alarm_type(
            display_name=display_name,
            image_base64=image_b64,
        )
        result.update({
            "alarm_uuid": alarm_uuid,
            "alarm_type": alarm_type,
            "display_name": display_name,
            "img_url": full_url,
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
        name="大模型复判 AI 告警",
        description=(
            "读取 AI 告警截图，用多模态大模型复判该告警是否真实。"
            "传入 alarm_uuid（推荐，自动从平台读取 img_path 与 event_type），"
            "或直接传 image_path/image_url + alarm_type。"
            "返回 verdict(confirmed/rejected/uncertain)、confidence、reasoning。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "alarm_uuid": {"type": "string", "description": "AI 事件 UUID（推荐）"},
                "image_path": {"type": "string",
                               "description": "图片路径或 URL（不传 alarm_uuid 时使用）"},
                "alarm_type": {"type": "string", "description": "告警类型编码或中文名（配合 image_path 使用）"},
            },
        },
        implementation=vlm_judge_impl,
        skill_type=SkillType.SUBGRAPH,
        tags=["vlm", "judge", "multimodal", "ai_event"],
    ))
    logger.info("复判子图 Skill 已注册（真实平台版）: vlm_judge_alarm")
