"""告警业务 Skills（阶段 2.5.1：消费真实平台 MCP 输出）

4 个本地 TOOL：
    - aggregate_alarms: 聚合统计（基于 ai_event_list）
    - visualize_alarms: 生成 base64 PNG 柱状图/折线图/饼图
    - fetch_alarm_context: 录像片段查询（基于 video_resolve_camera_channel + video_record_find_segments）
    - update_alarm_status: 复判结论回写（基于 ai_event_deal）

字段对齐真实平台（与 SQLite 时代差异见下表）：
    旧（SQLite）       -> 新（MCP/真实平台）
    alarm_uuid         -> events[].uuid
    alarm_type         -> events[].event_type（编码 ET03007）
    alarm_name         -> events[].event_name（中文，如 "未戴安全帽告警"）
    snapshot_url       -> events[].img_path（相对路径，需拼 om_base_url）
    ts_event(epoch)    -> events[].created_at（"yyyy-MM-dd HH:mm:ss"）
    camera_id          -> events[].camera_uuid 或 events[].camera_name
    status: closed/false_alarm  -> ai_event_deal.review_status: 2(已复核)/3(已完成)/5(误报)
"""
import asyncio
import base64
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # 无头环境
import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm
from loguru import logger

from skills.base import Skill, SkillType


# ------------------ 中文字体（保留原逻辑） ------------------
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
for _fp in _CJK_FONT_CANDIDATES:
    if Path(_fp).exists():
        try:
            _fm.fontManager.addfont(_fp)
            _name = _fm.FontProperties(fname=_fp).get_name()
            matplotlib.rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            logger.info(f"[viz] 使用中文字体: {_name}")
            break
        except Exception:
            continue


def _normalize_time(s: str | None, end_of_day: bool = False) -> str | None:
    """把 'YYYY-MM-DD' 补全为 'YYYY-MM-DD HH:mm:ss'，已带时分秒则原样返回"""
    if not s:
        return None
    s = s.strip()
    if len(s) == 10:  # 仅日期
        return s + (" 23:59:59" if end_of_day else " 00:00:00")
    return s


# ===================== Skill 1: 聚合统计 =====================
async def aggregate_alarms_impl(args: dict, context: dict) -> dict:
    """聚合统计 AI 视觉告警（消费 ai_event_list）

    支持 group_by: event_type / event_name / date / camera / level
    向后兼容：alarm_type → event_type, camera_id → camera
    """
    from skills import get_skill_registry
    registry = get_skill_registry()

    group_by = args.get("group_by", "event_name")
    # 向后兼容旧参数名
    alias = {"alarm_type": "event_type", "camera_id": "camera"}
    group_by = alias.get(group_by, group_by)
    if group_by not in ("event_type", "event_name", "date", "camera", "level"):
        return {"error": f"group_by 必须是 event_type/event_name/date/camera/level，收到: {group_by}"}

    base_args: dict[str, Any] = {}
    if t := _normalize_time(args.get("date_start") or args.get("time_start"), False):
        base_args["time_start"] = t
    if t := _normalize_time(args.get("date_end") or args.get("time_end"), True):
        base_args["time_end"] = t
    if et := args.get("alarm_type") or args.get("event_type"):
        base_args["event_type"] = et
    if lv := args.get("level"):
        base_args["level"] = lv

    # 自动分页拉取全量数据（在同一事件循环内完成所有分页，避免统计采样不全）
    # 之前单页 pagesize=10000，当 total > 10000 时只统计了首页，导致"10529 条只统计 10000 条"
    PAGE_SIZE = 10000
    MAX_PAGES = 100  # 上限保护：100 * 10000 = 100 万条
    events: list[dict] = []
    total = 0
    pageno = 1
    while pageno <= MAX_PAGES:
        page_args = dict(base_args)
        page_args["pageno"] = pageno
        page_args["pagesize"] = PAGE_SIZE
        result = await registry.invoke("ai_event_list", page_args, context)
        if result.get("error"):
            # 首页就失败则直接返回错误；后续页失败则保留已拉取数据
            if pageno == 1:
                return {"error": result["error"]}
            logger.warning(f"[aggregate_alarms] 第 {pageno} 页拉取失败，停止分页，已拉取 {len(events)} 条")
            break

        page_events = result.get("events", []) or []
        total = result.get("total", len(page_events))
        events.extend(page_events)

        if len(events) >= total or len(page_events) < PAGE_SIZE:
            break
        pageno += 1

    if len(events) < total:
        logger.warning(f"[aggregate_alarms] 分页拉取 {len(events)}/{total} 条（未拉满，可能触发 MAX_PAGES 上限）")
    else:
        logger.info(f"[aggregate_alarms] 分页拉取完成，共 {len(events)}/{total} 条")

    counts: dict[str, int] = {}
    for e in events:
        if group_by == "event_type":
            key = e.get("event_type") or "unknown"
        elif group_by == "event_name":
            key = e.get("event_name") or e.get("event_type") or "unknown"
        elif group_by == "date":
            ca = e.get("created_at") or ""
            key = ca.split(" ", 1)[0] or "unknown"
        elif group_by == "camera":
            key = e.get("camera_name") or e.get("camera_uuid") or "unknown"
        else:  # level
            key = e.get("level") or "unknown"
        counts[key] = counts.get(key, 0) + 1

    # date 按时间升序，其余按数量降序
    if group_by == "date":
        items = sorted(counts.items(), key=lambda kv: kv[0])
    else:
        items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    data = [{"key": k, "count": v} for k, v in items]
    return {
        "group_by": group_by,
        "data": data,
        "total": sum(v for _, v in items),
        "platform_total": total,  # 平台返回的 total（若 > 拉取量，提示采样）
        "sampled": total > len(events),
        "error": None,
    }


# ===================== Skill 2: 可视化 =====================
def visualize_alarms_impl(args: dict, context: dict) -> dict:
    """聚合数据 → base64 PNG（柱状图/折线图/饼图）"""
    data = args.get("data")
    # 兼容传入 aggregate_alarms 的完整输出
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not data or not isinstance(data, list):
        return {"error": "data 为空或格式错误（需 [{key, count}, ...]）"}

    chart_type = args.get("chart_type", "bar")
    title = args.get("title", "AI 告警统计")
    labels = [str(d.get("key")) for d in data]
    values = [d.get("count", 0) for d in data]

    try:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        if chart_type == "line":
            ax.plot(labels, values, marker="o", color="#2c7be5", linewidth=2)
            ax.set_ylabel("数量")
            for x, y in zip(labels, values):
                ax.annotate(str(y), (x, y), textcoords="offset points", xytext=(0, 6), ha="center")
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        else:  # bar
            bars = ax.bar(labels, values, color="#2c7be5")
            ax.set_ylabel("数量")
            ax.bar_label(bars)

        ax.set_title(title)
        if chart_type != "pie":
            plt.xticks(rotation=30, ha="right")
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.exception("[visualize] 绘图失败")
        return {"error": f"绘图失败: {type(e).__name__}: {e}"}

    return {
        "image_base64": f"data:image/png;base64,{b64}",
        "chart_type": chart_type,
        "title": title,
        "error": None,
    }


# ===================== Skill 3: 录像回溯 =====================
async def fetch_alarm_context_impl(args: dict, context: dict) -> dict:
    """获取告警前后 N 秒的录像片段

    新流程：
        1. ai_event_detail(event_uuid) -> camera_name, created_at
        2. video_resolve_camera_channel(camera_name) -> channel.uuid
        3. video_record_find_segments(channel_uuid, start_ms, end_ms)
    """
    from skills import get_skill_registry
    registry = get_skill_registry()

    alarm_uuid = args.get("alarm_uuid") or args.get("event_uuid")
    if not alarm_uuid:
        return {"error": "缺少 alarm_uuid / event_uuid"}
    before_sec = int(args.get("before_sec", 10))
    after_sec = int(args.get("after_sec", 10))

    # 1. 拉详情
    detail = await registry.invoke("ai_event_detail", {"event_uuid": alarm_uuid}, context)
    if detail.get("error"):
        return {"error": f"ai_event_detail 失败: {detail['error']}"}
    event = detail.get("event") or {}
    camera_name = event.get("camera_name")
    created_at = event.get("created_at")
    if not camera_name or not created_at:
        return {"error": "事件缺少 camera_name 或 created_at"}

    # 时间窗（毫秒）
    try:
        t0 = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"error": f"created_at 解析失败: {created_at}"}
    start_ms = int((t0 - timedelta(seconds=before_sec)).timestamp() * 1000)
    end_ms = int((t0 + timedelta(seconds=after_sec)).timestamp() * 1000)

    # 2. 解析摄像机通道
    resolve = await registry.invoke(
        "video_resolve_camera_channel", {"camera_name": camera_name}, context
    )
    if resolve.get("error"):
        return {"error": f"解析摄像机失败: {resolve['error']}"}
    channel = (resolve.get("channel") or {})
    channel_uuid = channel.get("uuid")
    if not channel_uuid:
        return {
            "error": "未匹配到通道",
            "camera_name": camera_name,
            "candidates": resolve.get("candidates", []),
        }

    # 3. 录像片段
    segs = await registry.invoke(
        "video_record_find_segments",
        {"channel_uuid": channel_uuid, "start_time_ms": start_ms, "end_time_ms": end_ms},
        context,
    )
    if segs.get("error"):
        return {"error": f"查询录像片段失败: {segs['error']}"}

    return {
        "alarm_uuid": alarm_uuid,
        "camera_name": camera_name,
        "channel_uuid": channel_uuid,
        "created_at": created_at,
        "window": {"start_ms": start_ms, "end_ms": end_ms,
                   "before_sec": before_sec, "after_sec": after_sec},
        "segment_count": segs.get("segment_count", 0),
        "segments": segs.get("segments", []),
        "error": None,
    }


# ===================== Skill 4: 状态回写（包装 ai_event_deal） =====================
# verdict -> review_status（真实平台语义，见 tool_meta：1=确认 2=完成 3=误报 5=忽略）
_VERDICT_TO_REVIEW_STATUS = {
    "confirmed": 2,   # 确认有效，标记为已复核完成
    "rejected": 3,    # 误报
}


async def update_alarm_status_impl(args: dict, context: dict) -> dict:
    """复判结论回写（调用 ai_event_deal）

    支持参数：
        - alarm_uuid / event_uuid: 单个告警 UUID
        - event_uuid_list: UUID 列表（批量）
        - verdict: confirmed/rejected（自动映射为 review_status）
        - review_status: 直接传 1-5 整数（与 verdict 二选一）
        - note: 备注（写入 remark）
    """
    from skills import get_skill_registry
    registry = get_skill_registry()

    alarm_uuid = args.get("alarm_uuid") or args.get("event_uuid")
    uuid_list = args.get("event_uuid_list")
    if not uuid_list and alarm_uuid:
        uuid_list = [alarm_uuid]
    if not uuid_list:
        return {"error": "缺少 alarm_uuid / event_uuid / event_uuid_list"}

    review_status = args.get("review_status")
    if review_status is None:
        verdict = args.get("verdict")
        if not verdict:
            return {"error": "缺少 verdict 或 review_status"}
        review_status = _VERDICT_TO_REVIEW_STATUS.get(verdict)
        if review_status is None:
            return {"error": f"verdict={verdict} 不支持自动映射（仅支持 confirmed/rejected），"
                             "uncertain 请人工处理；或直接传 review_status"}
    review_status = int(review_status)

    payload = {
        "event_uuid": uuid_list,
        "review_status": review_status,
    }
    if note := args.get("note") or args.get("remark"):
        payload["remark"] = note

    result = await registry.invoke("ai_event_deal", payload, context)
    if result.get("error"):
        return {"error": result["error"]}

    return {
        "success": bool(result.get("updated", True)),
        "event_uuid": result.get("event_uuid", uuid_list),
        "review_status": result.get("review_status", review_status),
        "verdict": args.get("verdict"),
        "error": None,
    }


# ===================== 统一注册 =====================
def register_alarm_skills(registry):
    registry.register(Skill(
        id="aggregate_alarms", name="AI 告警聚合统计",
        description=(
            "聚合统计 AI 视觉告警（基于真实平台 ai_event_list）。"
            "group_by 取值：event_name(中文名,推荐)/event_type(算法编码)/date(按天)/camera(按摄像机)/level(级别)。"
            "时间格式 YYYY-MM-DD（自动补全时分秒）。返回 data 列表供 visualize_alarms 使用。"
        ),
        parameters={"type": "object", "properties": {
            "group_by": {"type": "string",
                         "description": "分组维度: event_name/event_type/date/camera/level",
                         "default": "event_name"},
            "date_start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
            "date_end": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选，含当天）"},
            "event_type": {"type": "string", "description": "筛选特定算法编码（如 ET03007）"},
            "level": {"type": "string", "description": "告警级别 red/orange/yellow/blue（可选）"},
        }},
        implementation=aggregate_alarms_impl, skill_type=SkillType.TOOL,
        tags=["data", "stats", "ai_event"],
    ))
    registry.register(Skill(
        id="visualize_alarms", name="告警可视化",
        description="将聚合统计结果绘制成柱状图/折线图/饼图，返回 base64 PNG。"
                    "data 直接传 aggregate_alarms 的输出（{{step_N}}）。",
        parameters={"type": "object", "properties": {
            "data": {"description": "聚合数据（aggregate_alarms 输出，或 [{key,count}] 列表）"},
            "chart_type": {"type": "string", "description": "图表类型 line/bar/pie", "default": "bar"},
            "title": {"type": "string", "description": "图表标题"},
        }, "required": ["data"]},
        implementation=visualize_alarms_impl, skill_type=SkillType.TOOL, tags=["viz"],
    ))
    registry.register(Skill(
        id="fetch_alarm_context", name="告警录像回溯",
        description=(
            "获取某 AI 告警发生前后 N 秒的录像片段（依次调用 ai_event_detail → "
            "video_resolve_camera_channel → video_record_find_segments）。"
        ),
        parameters={"type": "object", "properties": {
            "alarm_uuid": {"type": "string", "description": "AI 事件 UUID"},
            "before_sec": {"type": "integer", "description": "告警前秒数", "default": 10},
            "after_sec": {"type": "integer", "description": "告警后秒数", "default": 10},
        }, "required": ["alarm_uuid"]},
        implementation=fetch_alarm_context_impl, skill_type=SkillType.TOOL,
        tags=["data", "video", "ai_event"],
    ))
    registry.register(Skill(
        id="update_alarm_status", name="回写 AI 告警复核状态",
        description=(
            "将复判结论写回平台（ai_event_deal）。推荐传 verdict=\"{{step_N.verdict}}\" "
            "引用复判子图输出，自动映射：confirmed→review_status=2(已复核完成)、"
            "rejected→review_status=3(误报)。也可直接传 review_status (1-5)。"
        ),
        parameters={"type": "object", "properties": {
            "alarm_uuid": {"type": "string", "description": "单个事件 UUID"},
            "event_uuid_list": {"type": "array", "items": {"type": "string"},
                                "description": "批量事件 UUID 列表（与 alarm_uuid 二选一）"},
            "verdict": {"type": "string",
                        "description": "复判结论 confirmed/rejected（推荐用 {{step_N.verdict}}）"},
            "review_status": {"type": "integer",
                              "description": "直接指定 1=确认 2=已复核 3=误报 5=忽略（与 verdict 二选一）"},
            "note": {"type": "string", "description": "复判备注（写入 remark）"},
        }, "required": ["alarm_uuid"]},
        implementation=update_alarm_status_impl, skill_type=SkillType.TOOL,
        tags=["data", "write", "ai_event"],
    ))
    logger.info("告警业务 Skills 已注册（真实平台版）: aggregate_alarms, visualize_alarms, "
                "fetch_alarm_context, update_alarm_status")
