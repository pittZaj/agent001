"""告警业务 Skills：聚合统计、可视化、录像回溯、状态回写。

包含 4 个本地 TOOL：
    - aggregate_alarms: 按时间/类型/摄像头聚合统计
    - visualize_alarms: 生成折线图/柱状图/饼图（base64 PNG）
    - fetch_alarm_context: 获取告警前后 N 秒录像片段
    - update_alarm_status: 复判结论回写（包装只写 MCP server 实现）
"""
import base64
import io
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # 无头环境
import matplotlib.pyplot as plt
from loguru import logger

from skills.base import Skill, SkillType

DB_PATH = Path(__file__).resolve().parent.parent / "agent" / "data" / "ksipms_dev.db"

import matplotlib
matplotlib.use("Agg")  # 无头环境
import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm
from loguru import logger

from skills.base import Skill, SkillType

DB_PATH = Path(__file__).resolve().parent.parent / "agent" / "data" / "ksipms_dev.db"

# 中文字体：注册系统中实际存在且同时覆盖中文+数字+拉丁的 CJK 字体文件
# 注意：DroidSansFallback 只含中文不含数字/拉丁，必须用 Noto CJK（三者全覆盖）
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


def _ro_conn():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _date_to_epoch(date_str: str, end: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    return ts + 86400 if end else ts


# ===================== Skill 1: 聚合统计 =====================
def aggregate_alarms_impl(args: dict, context: dict) -> dict:
    """按 date/alarm_type/camera_id 聚合统计告警数"""
    group_by = args.get("group_by", "alarm_type")
    if group_by not in ("date", "alarm_type", "camera_id"):
        return {"error": f"group_by 必须是 date/alarm_type/camera_id，收到: {group_by}"}

    date_start = args.get("date_start")
    date_end = args.get("date_end")
    alarm_type = args.get("alarm_type")

    where = ["1=1"]
    params: list[Any] = []
    if date_start:
        where.append("ts_event >= ?"); params.append(_date_to_epoch(date_start))
    if date_end:
        where.append("ts_event < ?"); params.append(_date_to_epoch(date_end, end=True))
    if alarm_type:
        where.append("alarm_type = ?"); params.append(alarm_type)

    if group_by == "date":
        select_key = "strftime('%Y-%m-%d', datetime(ts_event,'unixepoch')) AS k"
        order = "k ASC"
    else:
        select_key = f"{group_by} AS k"
        order = "cnt DESC"

    sql = f"SELECT {select_key}, COUNT(*) AS cnt FROM alarms WHERE {' AND '.join(where)} GROUP BY k ORDER BY {order}"
    try:
        conn = _ro_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"error": f"sqlite error: {e}"}

    data = [{"key": r["k"], "count": r["cnt"]} for r in rows]
    return {
        "group_by": group_by,
        "data": data,
        "total": sum(d["count"] for d in data),
        "error": None,
    }


# ===================== Skill 3: 录像回溯 =====================
def fetch_alarm_context_impl(args: dict, context: dict) -> dict:
    """获取告警前后 N 秒的录像片段"""
    alarm_uuid = args.get("alarm_uuid")
    before_sec = int(args.get("before_sec", 10))
    after_sec = int(args.get("after_sec", 10))
    if not alarm_uuid:
        return {"error": "缺少 alarm_uuid"}

    try:
        conn = _ro_conn()
        try:
            alarm = conn.execute(
                "SELECT camera_id, ts_event FROM alarms WHERE alarm_uuid=?", (alarm_uuid,)
            ).fetchone()
            if not alarm:
                return {"error": f"告警不存在: {alarm_uuid}"}
            cam, ts = alarm["camera_id"], alarm["ts_event"]
            t0, t1 = ts - before_sec, ts + after_sec

            # 查与该时间窗重叠的录像片段
            clips = conn.execute(
                "SELECT clip_id, camera_id, ts_start, ts_end, file_path FROM video_clips "
                "WHERE camera_id=? AND ts_start <= ? AND ts_end >= ? ORDER BY ts_start",
                (cam, t1, t0),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"error": f"sqlite error: {e}"}

    return {
        "alarm_uuid": alarm_uuid,
        "camera_id": cam,
        "ts_event": ts,
        "window": {"start": t0, "end": t1, "before_sec": before_sec, "after_sec": after_sec},
        "clips": [dict(c) for c in clips],
        "clip_count": len(clips),
        "error": None,
    }


# ===================== Skill 2: 可视化 =====================
def visualize_alarms_impl(args: dict, context: dict) -> dict:
    """将聚合数据渲染为折线图/柱状图/饼图，返回 base64 PNG"""
    data = args.get("data")
    # 兼容传入 aggregate_alarms 的完整输出
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not data or not isinstance(data, list):
        return {"error": "data 为空或格式错误（需 [{key, count}, ...]）"}

    chart_type = args.get("chart_type", "bar")
    title = args.get("title", "告警统计")
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


# ===================== Skill 4: 状态回写（包装只写 MCP） =====================
# 复判结论 verdict -> 告警状态 status 的映射
_VERDICT_TO_STATUS = {"confirmed": "closed", "rejected": "false_alarm"}


def update_alarm_status_impl(args: dict, context: dict) -> dict:
    """复判结论回写（包装 mcp_servers.ksipms_write_server 的受控写实现）

    支持两种入参：
    - 直接传 status (closed/false_alarm)
    - 传 verdict (confirmed/rejected)，自动映射为 status（便于用 {{step_N.verdict}} 引用复判输出）
    """
    from mcp_servers.ksipms_write_server import update_alarm_status_impl as _write
    alarm_uuid = args.get("alarm_uuid")
    status = args.get("status")
    note = args.get("note", "")

    # verdict 自动映射
    verdict = args.get("verdict")
    if not status and verdict:
        status = _VERDICT_TO_STATUS.get(verdict)
        if not status:
            return {"error": f"verdict={verdict} 无法映射为状态（confirmed/rejected）；uncertain 不自动回写"}
    if not alarm_uuid or not status:
        return {"error": "缺少 alarm_uuid 或 status"}
    result = _write(alarm_uuid, status, note)
    if not result.get("success"):
        return {"error": result.get("error", "写回失败")}
    return result


# ===================== 统一注册 =====================
def register_alarm_skills(registry):
    registry.register(Skill(
        id="aggregate_alarms", name="告警聚合统计",
        description="按 日期/类型/摄像头 聚合统计告警数量。group_by 取值 date/alarm_type/camera_id。返回 data 列表供可视化使用。",
        parameters={"type": "object", "properties": {
            "group_by": {"type": "string", "description": "分组维度：date/alarm_type/camera_id", "default": "alarm_type"},
            "date_start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
            "date_end": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
            "alarm_type": {"type": "string", "description": "筛选特定告警类型（可选）"},
        }},
        implementation=aggregate_alarms_impl, skill_type=SkillType.TOOL, tags=["data", "stats"],
    ))
    registry.register(Skill(
        id="visualize_alarms", name="告警可视化",
        description="把聚合统计结果绘制成折线图/柱状图/饼图，返回 base64 PNG 图片。chart_type 取值 line/bar/pie。data 直接传 aggregate_alarms 的输出。",
        parameters={"type": "object", "properties": {
            "data": {"description": "聚合数据（aggregate_alarms 的输出或 [{key,count}] 列表）"},
            "chart_type": {"type": "string", "description": "图表类型：line/bar/pie", "default": "bar"},
            "title": {"type": "string", "description": "图表标题"},
        }, "required": ["data"]},
        implementation=visualize_alarms_impl, skill_type=SkillType.TOOL, tags=["viz"],
    ))
    registry.register(Skill(
        id="fetch_alarm_context", name="告警录像回溯",
        description="获取某告警发生前后 N 秒的录像片段。传入 alarm_uuid，自动定位摄像头和时间窗。",
        parameters={"type": "object", "properties": {
            "alarm_uuid": {"type": "string", "description": "告警UUID"},
            "before_sec": {"type": "integer", "description": "告警前秒数", "default": 10},
            "after_sec": {"type": "integer", "description": "告警后秒数", "default": 10},
        }, "required": ["alarm_uuid"]},
        implementation=fetch_alarm_context_impl, skill_type=SkillType.TOOL, tags=["data", "video"],
    ))
    registry.register(Skill(
        id="update_alarm_status", name="回写告警状态",
        description="将复判结论写回数据库并记审计日志。推荐用 verdict 参数引用复判子图的输出（如 verdict=\"{{step_0.verdict}}\"），"
                    "由系统自动映射：confirmed→closed、rejected→false_alarm。也可直接传 status(closed/false_alarm)。",
        parameters={"type": "object", "properties": {
            "alarm_uuid": {"type": "string", "description": "告警UUID"},
            "verdict": {"type": "string", "description": "复判结论 confirmed/rejected（推荐用 {{step_N.verdict}} 引用复判输出，自动映射为状态）"},
            "status": {"type": "string", "description": "直接指定状态 closed 或 false_alarm（与 verdict 二选一）"},
            "note": {"type": "string", "description": "处理备注（复判理由）"},
        }, "required": ["alarm_uuid"]},
        implementation=update_alarm_status_impl, skill_type=SkillType.TOOL, tags=["data", "write"],
    ))
    logger.info("告警业务 Skills 已注册: aggregate_alarms, visualize_alarms, fetch_alarm_context, update_alarm_status")
