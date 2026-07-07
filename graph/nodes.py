from typing import Dict, Any
import asyncio
import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from loguru import logger

from graph.state import AgentState
from utils import CONFIG
from skills import get_skill_registry
from graph.streaming import emit_status, stream_llm, tool_label, emit_token_chunked
from graph.memory import history_prompt_block
from graph.harness_guard import check_plan, needs_confirmation, check_result, is_guard_enabled, log_guard_action


# T1：Plan 的 JSON Schema（guided_json 约束，确保 planner 必出合法任务数组）
PLAN_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["task", "args"],
    },
}

# T4：降级路径友好化（6.1.4）
# planner 解析失败 / 规划异常时，对用户只展示这段友好澄清话术，绝不回显 LLM 原文或异常堆栈
# （那会泄露内部 JSON 约定与实现细节）；原始内容仅落日志，便于排查。两处降级分支共用。
PLANNER_FALLBACK_HINT = (
    "抱歉，我没太理解你的问题。你可以换种方式描述，例如：\n"
    "· 查询某类告警：「查询未戴安全帽的告警」\n"
    "· 统计分析：「统计每种告警类型数量并画柱状图」\n"
    "· 检索规章：「未戴安全帽违反哪些规定」"
)


def _first_line(text: str, limit: int = 72) -> str:
    line = (text or "").strip().split("\n")[0].strip()
    if len(line) > limit:
        return line[: limit - 1] + "…"
    return line


def _format_skills_compact(skills) -> str:
    """Planner 专用：仅 tool id + 一行摘要，不含参数 schema（控制 token）。"""
    groups: dict[str, list] = {
        "ai": [],
        "video/record/compress": [],
        "system": [],
        "local": [],
        "other": [],
    }
    for s in skills:
        sid = s.id
        if sid.startswith("ai_"):
            groups["ai"].append(s)
        elif sid.startswith(("video_", "record_", "compress_")):
            groups["video/record/compress"].append(s)
        elif sid.startswith("system_"):
            groups["system"].append(s)
        elif sid in ("direct_response", "stream_chat"):
            groups["other"].append(s)
        else:
            groups["local"].append(s)

    title_map = {
        "ai": "AI",
        "video/record/compress": "视频/录像/压缩",
        "system": "系统",
        "local": "本地分析/子图",
        "other": "基础",
    }
    lines: list[str] = []
    for key, items in groups.items():
        if not items:
            continue
        lines.append(f"## {title_map[key]}")
        for s in sorted(items, key=lambda x: x.id):
            lines.append(f"- `{s.id}`: {_first_line(s.description)}")
    return "\n".join(lines)


def _format_skills_grouped(skills) -> str:
    """按真实平台分类组织工具描述（ai_*/video_*/system_* / 本地分析 / 子图）"""
    groups: dict[str, list] = {
        "AI 视觉告警 (ai_*)": [],
        "视频设备与录像 (video_*)": [],
        "系统管理 (system_*)": [],
        "数据分析 (本地)": [],
        "复杂子图": [],
        "基础": [],
    }
    for s in skills:
        if s.id.startswith("ai_"):
            groups["AI 视觉告警 (ai_*)"].append(s)
        elif s.id.startswith("video_"):
            groups["视频设备与录像 (video_*)"].append(s)
        elif s.id.startswith("system_"):
            groups["系统管理 (system_*)"].append(s)
        elif s.skill_type.value == "subgraph":
            groups["复杂子图"].append(s)
        elif s.id == "direct_response":
            groups["基础"].append(s)
        else:
            groups["数据分析 (本地)"].append(s)

    lines: list[str] = []
    for title, items in groups.items():
        if not items:
            continue
        lines.append(f"\n## {title}")
        for s in items:
            params = s.parameters.get("properties", {}) if isinstance(s.parameters, dict) else {}
            param_desc = ", ".join([
                f"{k}: {v.get('description', v.get('type', 'any'))}"
                for k, v in list(params.items())[:6]  # 截断长 schema
            ])
            lines.append(f"- `{s.id}` — {s.description}")
            if param_desc:
                lines.append(f"   参数: {{{param_desc}}}")
    return "\n".join(lines)


# ===================== 平台支持的告警类型（权威静态字典） =====================
# 关键修复（区分"支持"与"有数据"两件事）：
#   - 旧做法从 ai_event_list 数据反推类型，只能发现"有记录"的类型（当前只有 2 种），
#     会把"平台支持但暂无数据"的类型（如未戴口罩 ET03004）误判成"平台不存在该告警"。
#   - 新做法以平台后端算法宏定义（KSAI_* #define）为权威字典 → 准确回答"平台是否支持识别"，
#     "是否有数据"则交给实际查询 ai_event_list 的 total 判断（formatter 空结果守卫处理）。
from skills.event_types import (
    catalog_lines as _supported_catalog_lines,  # 同事遗漏的导入
    catalog_inline as _supported_catalog_inline,
    normalize_event_type,
    resolve_event_type_from_text,
    display_name as _event_display_name,
)


def _needs_event_catalog(user_message: str) -> bool:
    """非告警/统计类问题（如纯录像/直播/压缩）省略完整 event_type 清单以节省 token。"""
    msg = (user_message or "").strip()
    video_kw = ("录像", "直播", "压缩", "主码流", "子码流", "回放", "flv", "预览")
    alarm_kw = ("告警", "报警", "违规", "安全帽", "抽烟", "离岗", "入侵", "明火", "人脸", "统计", "复判")
    if any(k in msg for k in video_kw) and not any(k in msg for k in alarm_kw):
        return False
    return True

# 口语里常见的算法别名（补充 event_types 字典里的正式名称）
_EVENT_TYPE_HINTS = (
    "安全帽", "抽烟", "口罩", "护目镜", "安全带", "工作服", "明火", "烟雾", "灭火器",
    "离岗", "入侵", "聚集", "徘徊", "跌倒", "打瞌睡", "打架", "攀爬", "打电话", "手机",
    "人脸",
)


def _user_mentions_specific_event_type(msg: str) -> bool:
    """用户是否明确提到了某一类 AI 算法（而非泛指的「AI告警」）。"""
    from skills.event_types import SUPPORTED_EVENT_TYPES
    text = (msg or "").strip()
    if not text:
        return False
    for name in SUPPORTED_EVENT_TYPES.values():
        if name in text:
            return True
    return any(h in text for h in _EVENT_TYPE_HINTS)


def _extract_camera_hint_from_message(msg: str) -> str | None:
    """从「统计公司大门口今天的AI告警」类问句中提取摄像机/地点名。"""
    import re
    s = (msg or "").strip()
    if not s:
        return None
    for pat in (
        r"^(?:请|帮我|帮忙|查一下|查询|查看|列出|显示|统计)?",
        r"(?:今天|昨天|前天|明日|\d{4}-\d{2}-\d{2})(?:的)?",
        r"(?:最近\d+天?)(?:的)?",
        r"(?:的)?(?:AI|ai)?(?:视觉)?(?:算法)?告警(?:事件|记录|情况|数据)?",
        r"(?:有多少|多少条|数量|分布|明细|汇总|统计)?$",
    ):
        s = re.sub(pat, "", s).strip()
    s = s.strip("的 ，,、")
    if len(s) < 2:
        return None
    from skills.event_types import SUPPORTED_EVENT_TYPES
    if any(s == name or s in name for name in SUPPORTED_EVENT_TYPES.values()):
        return None
    return s


def _ensure_today_time_range(args: dict, msg: str) -> dict:
    """用户提到「今天」且未指定时间时，自动补全当日 00:00:00–23:59:59。"""
    if "今天" not in (msg or ""):
        return args
    if any(args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
        return args
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    args["time_start"] = f"{today} 00:00:00"
    args["time_end"] = f"{today} 23:59:59"
    return args


def _message_text(msg) -> str:
    """兼容 BaseMessage / 多模态 content 的纯文本提取。"""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict):
                if seg.get("type") == "text" or "text" in seg:
                    parts.append(str(seg.get("text", "")))
            else:
                parts.append(str(seg))
        content = " ".join(p for p in parts if p)
    return str(content or "").strip()


def _infer_relative_time_range(msg: str) -> dict | None:
    """从用户原话里提取相对时间范围，供多轮上下文继承使用。"""
    from datetime import datetime, timedelta

    s = (msg or "").strip()
    if not s:
        return None

    now = datetime.now()
    if "今天" in s:
        day = now.strftime("%Y-%m-%d")
        return {"time_start": f"{day} 00:00:00", "time_end": f"{day} 23:59:59"}
    if "昨天" in s:
        day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        return {"time_start": f"{day} 00:00:00", "time_end": f"{day} 23:59:59"}
    if "前天" in s:
        day = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        return {"time_start": f"{day} 00:00:00", "time_end": f"{day} 23:59:59"}

    m = re.search(r"最近\s*(\d+)\s*天", s)
    if m:
        days = max(1, int(m.group(1)))
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
        end = now.strftime("%Y-%m-%d %H:%M:%S")
        return {"time_start": start, "time_end": end}

    return None


def _user_has_time_hint(msg: str) -> bool:
    return _infer_relative_time_range(msg) is not None


def _user_wants_followup_context(msg: str) -> bool:
    text = (msg or "").strip()
    return any(k in text for k in (
        "前3条", "前几条", "最新", "最近一条", "最新一条", "这条", "那条", "它", "这个",
        "刚才", "上一条", "上次", "继续", "再查", "再看", "同样", "相同", "沿用",
    ))


def _extract_pagesize_from_message(msg: str) -> int | None:
    """从用户原话里提取最近/前 N 条的页大小。"""
    text = (msg or "").strip()
    if not text:
        return None
    if any(k in text for k in ("最新", "最近一条", "最新一条", "最后一条")):
        return 1
    m = re.search(r"前\s*(\d+)\s*条", text)
    if m:
        return max(1, int(m.group(1)))
    if "前几条" in text:
        return 3
    return None


def _extract_alarm_context_from_history(messages) -> dict:
    """从会话历史提取最近一次明确的告警类型与时间范围。"""
    ctx = {"event_type": None, "time_range": None}
    for msg in messages or []:
        if not isinstance(msg, HumanMessage):
            continue
        text = _message_text(msg)
        if not text:
            continue
        if et := resolve_event_type_from_text(text):
            ctx["event_type"] = et
        if tr := _infer_relative_time_range(text):
            ctx["time_range"] = tr
    return ctx


def _user_wants_stats(msg: str) -> bool:
    return any(k in (msg or "") for k in ("统计", "多少", "数量", "分布", "汇总", "共"))


def _normalize_alarm_plan(plan: list, user_message: str, history_messages=None) -> list:
    """修正 Planner 计划：规范 event_type、统计类查询走 aggregate_alarms。"""
    if not plan:
        return plan

    msg = user_message or ""
    stats_kw = _user_wants_stats(msg)
    alarm_kw = any(k in msg for k in ("告警", "报警", "AI", "事件"))
    resolved_from_msg = resolve_event_type_from_text(msg)
    history_ctx = _extract_alarm_context_from_history(history_messages or [])

    # 逐步规范 event_type
    for task in plan:
        args = dict(task.get("args") or {})
        if raw_et := args.get("event_type"):
            if norm := normalize_event_type(raw_et):
                args["event_type"] = norm
        elif resolved_from_msg and (_user_mentions_specific_event_type(msg) or stats_kw):
            args["event_type"] = resolved_from_msg
        elif history_ctx.get("event_type") and _user_wants_followup_context(msg) and not _user_has_time_hint(msg):
            args["event_type"] = history_ctx["event_type"]
        task["args"] = args

    if len(plan) != 1:
        return plan

    task = plan[0]
    tool = task.get("task", "")
    args = dict(task.get("args") or {})
    camera_hint = args.get("camera_name") or _extract_camera_hint_from_message(msg)
    resolved_et = args.get("event_type") or resolved_from_msg or history_ctx.get("event_type")
    time_hint = _infer_relative_time_range(msg) or history_ctx.get("time_range")
    pagesize_hint = _extract_pagesize_from_message(msg)

    # 历史跟进问题（如「查询最新一条告警事件」）即使本轮没写明类型，也沿用上一轮告警类型。
    if (
        history_ctx.get("event_type")
        and _user_wants_followup_context(msg)
        and alarm_kw
        and tool in ("direct_response", "stream_chat")
    ):
        follow_args: dict = {"event_type": history_ctx["event_type"]}
        if pagesize_hint is not None:
            follow_args["pagesize"] = pagesize_hint
        if time_hint:
            follow_args.update(time_hint)
        elif history_ctx.get("time_range"):
            follow_args.update(history_ctx["time_range"])
        else:
            _ensure_today_time_range(follow_args, msg)
        logger.info(f"[Planner] 历史跟进问题 → ai_event_list {follow_args}")
        return [{"task": "ai_event_list", "args": follow_args, "status": "pending"}]

    # 统计 + 明确类型 → aggregate_alarms（按摄像头分布）
    if stats_kw and resolved_et and tool in ("ai_event_list", "aggregate_alarms"):
        new_args: dict = {
            "event_type": resolved_et,
            "group_by": args.get("group_by") or "camera",
        }
        if pagesize_hint is not None:
            new_args["pagesize"] = pagesize_hint
        if camera_hint:
            new_args["camera_name"] = camera_hint
        for k in ("time_start", "time_end", "date_start", "date_end", "level", "review_status"):
            if v := args.get(k):
                new_args[k] = v
        if time_hint and not any(new_args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
            new_args.update(time_hint)
        _ensure_today_time_range(new_args, msg)
        logger.info(f"[Planner] 统计类查询 → aggregate_alarms event_type={resolved_et} {new_args}")
        return [{"task": "aggregate_alarms", "args": new_args, "status": "pending"}]

    if tool == "ai_event_list" and args.get("event_type") and not _user_mentions_specific_event_type(msg):
        if stats_kw and alarm_kw:
            new_args: dict = {"group_by": "event_name"}
            if pagesize_hint is not None:
                new_args["pagesize"] = pagesize_hint
            if camera_hint:
                new_args["camera_name"] = camera_hint
            for k in ("time_start", "time_end", "date_start", "date_end", "level", "review_status"):
                if v := args.get(k):
                    new_args[k] = v
            if time_hint and not any(new_args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
                new_args.update(time_hint)
            if "今天" in msg and not any(new_args.get(k) for k in ("time_start", "date_start")):
                _ensure_today_time_range(new_args, msg)
            logger.info(f"[Planner] 修正误解析：ai_event_list+event_type → aggregate_alarms {new_args}")
            return [{"task": "aggregate_alarms", "args": new_args, "status": "pending"}]

    if tool == "ai_event_list" and resolved_et:
        args["event_type"] = resolved_et
        if pagesize_hint is not None:
            args["pagesize"] = pagesize_hint
        if time_hint and not any(args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
            args.update(time_hint)
        _ensure_today_time_range(args, msg)
        return [{"task": tool, "args": args, "status": "pending"}]

    if tool == "aggregate_alarms":
        if resolved_et:
            args["event_type"] = resolved_et
        if pagesize_hint is not None:
            args["pagesize"] = pagesize_hint
        if time_hint and not any(args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
            args.update(time_hint)
        _ensure_today_time_range(args, msg)
        if camera_hint and not args.get("camera_name"):
            args["camera_name"] = camera_hint
        return [{"task": tool, "args": args, "status": "pending"}]

    if history_ctx.get("event_type") and tool in ("ai_event_list", "aggregate_alarms"):
        if not args.get("event_type"):
            args["event_type"] = history_ctx["event_type"]
        if pagesize_hint is not None:
            args["pagesize"] = pagesize_hint
        if time_hint and not any(args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
            args.update(time_hint)
        elif history_ctx.get("time_range") and not any(args.get(k) for k in ("time_start", "time_end", "date_start", "date_end")):
            args.update(history_ctx["time_range"])
        if tool == "aggregate_alarms":
            if not args.get("group_by"):
                args["group_by"] = "camera"
            if camera_hint and not args.get("camera_name"):
                args["camera_name"] = camera_hint
        return [{"task": tool, "args": args, "status": "pending"}]

    return plan


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    规划节点：LLM 解析用户意图，生成任务列表

    输入：user_message
    输出：plan（任务列表）
    """
    logger.info(f"[Planner] 开始规划任务，用户消息: {state['user_message']}")
    emit_status("planning", "正在理解你的问题并规划任务…")

    # 预路由：仅闲聊/纯规章走快路径；录像/告警/设备等统一由 Planner 解析
    from graph.pre_router import pre_route
    fast_result = pre_route(state["user_message"])
    if fast_result is not None:
        logger.info(f"[Planner] 预路由命中: {fast_result['route']}")
        return {
            "plan": fast_result["plan"],
            "current_task_idx": 0,
        }

    # 从 Skill Registry 获取可用工具，按平台分类组织
    registry = get_skill_registry()
    available_skills = registry.list_skills()
    tools_text = _format_skills_grouped(available_skills) or "暂无可用工具"

    # 注入"平台支持哪些 AI 告警类型"（权威静态字典，杜绝 LLM 凭空猜 event_type 编码）。
    # 注意：这里是"平台支持识别的类型"，不代表数据库当前一定有该类告警记录——
    # "有没有数据"由实际查询 ai_event_list 的结果决定（见 formatter 空结果守卫）。
    #
    # ⚠️ 关键优化：录像/直播/压缩等非告警问题跳过完整字典，节省 token（~1500 tokens）
    if _needs_event_catalog(state["user_message"]):
        catalog_text = _supported_catalog_lines()
        catalog_names = _supported_catalog_inline()
    else:
        # 精简版：仅列出类型名称，不展开完整规则
        catalog_text = ""
        catalog_names = _supported_catalog_inline()

    # 使用 LLM 客户端单例（避免每次重新实例化）
    from utils.llm_pool import get_llm
    llm = get_llm(role="planner", temperature=0.1)

    # T2：提示词分层 - 使用独立模块构建 system prompt
    from datetime import datetime
    from graph.planner_prompt import build_planner_system_prompt

    system_prompt = build_planner_system_prompt(
        tools_text=tools_text,
        catalog_text=catalog_text,
        catalog_names=catalog_names,
        now=datetime.now()
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["user_message"]),
    ]

    # 短期记忆：Planner 使用更短的历史窗口，避免超出模型 context
    history_block = history_prompt_block(
        state.get("messages", []),
        max_turns=3,
        max_chars=900,
        per_message_chars=300,
    )
    if history_block:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=history_block + "\n# 本轮用户问题\n" + state["user_message"]),
        ]

    try:
        # T1：结构化输出（guided_json）—— 让 LLM 必出合法 JSON 数组，省去正则解析
        use_guided = CONFIG["llm"].get("guided_json_enabled", True)

        if use_guided:
            try:
                # 方案 B（推荐）：bind extra_body 生成绑定副本，不污染单例缓存
                plan_llm = llm.bind(extra_body={"guided_json": PLAN_SCHEMA})
                response = plan_llm.invoke(messages)
                content = response.content

                # guided_json 下应直接是合法 JSON 数组
                plan = json.loads(content)
                logger.info(f"[Planner] 生成计划（guided_json）: {plan}")

            except Exception as e:
                logger.warning(f"[Planner] guided_json 失败，回退正则解析: {e}")
                use_guided = False

        # 正则路径兜底（开关关闭 或 guided_json 失败时走此路径）
        if not use_guided:
            response = llm.invoke(messages)
            content = response.content

            # 解析 JSON（原逻辑完全保留）
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                logger.info(f"[Planner] 生成计划（正则）: {plan}")
            else:
                # T4：降级路径友好化 - 原始输出仅落日志，用户侧只见友好话术
                logger.warning(f"[Planner] 无法解析计划，降级为友好提示。原始输出={content[:200]}")
                return {
                    "plan": [{"task": "direct_response", "args": {"text": PLANNER_FALLBACK_HINT}, "status": "pending"}],
                    "current_task_idx": 0,
                }

        # 统一后处理（两条路径汇合）
        plan = _normalize_alarm_plan(plan, state["user_message"], state.get("messages", []))

        # H6 挂载点：行动前 plan 硬校验（H5 骨架默认放行，H6 实施时填充实际逻辑）
        if is_guard_enabled(CONFIG):
            guard_result = check_plan(plan, registry)
            log_guard_action(guard_result, context="planner_node:check_plan")

            if not guard_result.allow:
                # 拦截：返回友好话术
                logger.warning(f"[Planner] plan 被护栏拦截: {guard_result.reason}")
                return {
                    "plan": [{"task": "direct_response", "args": {"text": guard_result.reason}, "status": "denied"}],
                    "current_task_idx": 0,
                }

            # 自动修正：使用修正后的 plan
            if guard_result.fixed_plan is not None:
                logger.info(f"[Planner] plan 被护栏自动修正")
                plan = guard_result.fixed_plan

        _tasks = [t.get("task", "") for t in plan]
        if _tasks and _tasks != ["direct_response"] and _tasks != ["stream_chat"]:
            emit_status("planned", "已规划 " + str(len(_tasks)) + " 个步骤："
                        + "、".join(tool_label(t) for t in _tasks))
        return {
            "plan": [{"task": t["task"], "args": t["args"], "status": "pending"} for t in plan],
            "current_task_idx": 0,
        }

    except Exception as e:
        # T4：降级路径友好化 - 异常细节仅落日志与 error 字段（供 debug 面板），用户侧只见友好话术
        logger.error(f"[Planner] 规划失败: {e}")
        return {
            "plan": [{"task": "direct_response", "args": {"text": PLANNER_FALLBACK_HINT}, "status": "failed"}],
            "current_task_idx": 0,
            "error": str(e),
        }


async def _fetch_all_events_async(registry, args: dict, context: dict, first_result: dict) -> dict:
    """ai_event_list 自动分页拉取全量数据（并发翻页优化）

    优化点：
    1. 并发拉取所有页（asyncio.gather + Semaphore 限流）
    2. 清理死代码（原 line 283-285 的重复赋值）
    3. 最多拉取 max_pages 页（防止无限循环）

    收益：
    - 3 页数据：串行 3s → 并发 1.5s（50%+ 提速）
    - 5 页数据：串行 5s → 并发 2s（60%+ 提速）
    """
    from graph.pagination import fetch_all_events_concurrent

    return await fetch_all_events_concurrent(
        invoke_func=registry.invoke,
        tool_name="ai_event_list",
        base_args=args,
        context=context,
        first_result=first_result,
        pagesize=10000,
        max_pages=100,
        max_concurrency=5,
    )


def _run_async(coro):
    """在同步图节点里安全运行协程：统一投递到进程级后台事件循环执行。

    🔧 根因修复（取代旧的 nest_asyncio / 每次 asyncio.run 方案）：
        MCP 的 streamablehttp_client 会开启一个与"创建它的任务"绑定的 anyio 取消
        作用域。旧方案每次工具调用都是新任务/新循环，复用连接后销毁时会"跨任务退出
        取消作用域"而崩溃（统计+画图这类多步任务必触发，并导致图表丢失）。

        改为把所有 MCP 协程投递到一个**永不中途关闭**的后台循环（utils.async_loop）。
        连接在该循环内创建一次、全程复用，取消作用域始终在同一循环里，杜绝崩溃；
        同时天然实现跨请求连接复用（优化 4.1 的真正目标）。
    """
    from utils.async_loop import run_on_background_loop
    return run_on_background_loop(coro)


def executor_node(state: AgentState) -> Dict[str, Any]:
    """
    执行节点：通过 Skill Registry 执行当前任务

    输入：plan, current_task_idx, step_outputs
    输出：tool_results, current_task_idx + 1, step_outputs (更新)

    支持步骤间传参：使用 {{step_N.field}} 语法引用前序步骤的输出
    """
    plan = state["plan"]
    idx = state["current_task_idx"]

    if idx >= len(plan):
        logger.info("[Executor] 所有任务已完成")
        return {"current_task_idx": idx}

    task = plan[idx]
    logger.info(f"[Executor] 执行任务 {idx + 1}/{len(plan)}: {task['task']}")
    if task["task"] not in ("direct_response", "stream_chat"):
        emit_status(
            "executing",
            "正在执行步骤 " + str(idx + 1) + "/" + str(len(plan))
            + "：" + tool_label(task["task"]) + "…",
        )

    # 步骤间传参：替换参数中的模板变量
    args = task["args"].copy() if task["args"] else {}
    step_outputs = state.get("step_outputs", {})

    args = _resolve_step_references(args, step_outputs, idx)

    # H7 挂载点：危险回写确认门（H5 骨架默认放行，H7 实施时填充实际逻辑）
    if is_guard_enabled(CONFIG):
        guard_result = needs_confirmation(task, CONFIG)
        log_guard_action(guard_result, context=f"executor_node:needs_confirmation[{task['task']}]")

        if not guard_result.allow:
            # 拦截：跳过该步，返回安全提示
            logger.warning(f"[Executor] 危险操作被护栏拦截: {task['task']} - {guard_result.reason}")
            result = {
                "success": False,
                "tool": task["task"],
                "error": f"[安全护栏] {guard_result.reason}"
            }
            # 更新任务状态并返回（跳过实际执行）
            plan[idx]["status"] = "blocked"
            plan[idx]["result"] = result
            tool_results = state.get("tool_results", [])
            tool_results.append(result)
            return {
                "plan": plan,
                "current_task_idx": idx + 1,
                "tool_results": tool_results,
                "step_outputs": step_outputs,
            }

    # 通过 Skill Registry 调用
    registry = get_skill_registry()
    context = {
        "session_id": state.get("session_id", ""),
        "trace_id": state.get("trace_id", ""),
    }

    # 同步包装异步调用（兼容 有/无 运行中事件循环 两种情况）
    try:
        if task["task"] == "ai_event_list":
            # T6：ai_event_list 排序兜底（6.2.2）—— 平台不支持排序参数，客户端兜底
            # 检测 `recent: true` 标志（由 planner 在"最近N条"语义下显式传递）：
            #   - 命中且 pagesize ≤ 100 → 全量拉取 + 按 created_at 降序 + 截断前 N
            #   - 命中但 pagesize > 100 → 拒绝并提示（避免无意义全量）
            #   - 未命中 → 保持原有"小 pagesize 不分页/大 pagesize 统计全量"逻辑
            user_pagesize = args.get("pagesize")
            is_recent = args.pop("recent", False)  # pop 避免传给平台（平台不认此参数）

            if is_recent:
                # "最近N条"语义：必须排序才能保证返回真正最新的
                if not user_pagesize or user_pagesize > 100:
                    # 阈值保护：超 100 条建议用时间筛选
                    result_data = {
                        "error": (
                            '「最近N条」建议 N ≤ 100（当前请求 N={}）。如需更大范围，'
                            '请改用时间筛选（如"查今天的告警"）以缩小候选集。'
                        ).format(user_pagesize or "全量")
                    }
                else:
                    async def _invoke_with_recent_sort():
                        """全量拉取 + 排序 + 截断，保证返回真正最新的 N 条"""
                        # 全量拉取（利用已有并发翻页基建）
                        first = await registry.invoke("ai_event_list", args, context)
                        if first.get("error"):
                            return first
                        full = await _fetch_all_events_async(registry, args, context, first)

                        # 按 created_at 降序（最新在前）
                        from graph.pagination import sort_events_by_created_at
                        all_events = full.get("events", [])
                        sorted_events = sort_events_by_created_at(all_events, desc=True)

                        # 截断前 N 条（真正最新）
                        result = dict(full)
                        result["events"] = sorted_events[:user_pagesize]
                        result["_sorted_and_truncated"] = True
                        logger.info(
                            f"[Executor] 「最近 {user_pagesize} 条」已排序：全量 {len(all_events)} 条 "
                            f"→ 按 created_at 降序 → 截断前 {user_pagesize} 条"
                        )
                        return result

                    result_data = _run_async(_invoke_with_recent_sort())
            else:
                # 原有逻辑（小 pagesize 不分页 / 大 pagesize 统计全量）
                should_fetch_all = user_pagesize is None or user_pagesize > 20

                async def _invoke_with_paging():
                    first = await registry.invoke("ai_event_list", args, context)
                    if first.get("error"):
                        return first
                    # 只有在需要统计全量时才自动分页
                    if should_fetch_all:
                        return await _fetch_all_events_async(registry, args, context, first)
                    else:
                        logger.info(
                            f"[Executor] ai_event_list 检测到小 pagesize={user_pagesize}，"
                            f"不自动拉全量（用户意图：查看样本）"
                        )
                        return first

                result_data = _run_async(_invoke_with_paging())
        else:
            result_data = _run_async(registry.invoke(task["task"], args, context))

        if result_data.get("error"):
            result = {
                "success": False,
                "tool": task["task"],
                "error": result_data["error"]
            }
        else:
            result = {
                "success": True,
                "tool": task["task"],
                "result": result_data
            }
    except Exception as e:
        logger.exception(f"[Executor] 任务执行失败: {task['task']}")
        result = {
            "success": False,
            "tool": task["task"],
            "error": f"{type(e).__name__}: {e}"
        }

    # 更新任务状态
    plan[idx]["status"] = "completed" if result.get("success") else "failed"
    plan[idx]["result"] = result

    # H7 审计日志：危险工具执行后强制留痕
    if is_guard_enabled(CONFIG):
        dangerous_tools = set(CONFIG.get("harness", {}).get("dangerous_tools", []))
        if task["task"] in dangerous_tools:
            # 危险工具执行后，记录审计日志
            status_str = "成功" if result.get("success") else "失败"
            args_summary = {
                "alarm_uuid": args.get("alarm_uuid") or args.get("event_uuid"),
                "verdict": args.get("verdict"),
                "review_status": args.get("review_status"),
                "source": args.get("source"),
                "confirmed_by_user": args.get("confirmed_by_user"),
            }
            logger.warning(
                f"[HARNESS-GUARD] 危险回写审计 {task['task']} {status_str}：{args_summary}"
            )

    tool_results = state.get("tool_results", [])
    tool_results.append(result)

    # 保存本步骤的输出，供后续步骤引用
    if result.get("success"):
        step_outputs[idx] = result.get("result", {})

    return {
        "plan": plan,
        "current_task_idx": idx + 1,
        "tool_results": tool_results,
        "step_outputs": step_outputs,
    }


def _resolve_step_references(args: Dict[str, Any], step_outputs: Dict[int, Any], current_idx: int) -> Dict[str, Any]:
    """
    递归解析参数中的步骤引用模板 {{step_N.field}}

    Args:
        args: 原始参数字典
        step_outputs: 已执行步骤的输出 {step_idx: result}
        current_idx: 当前步骤索引

    Returns:
        解析后的参数字典
    """
    resolved = {}

    for key, value in args.items():
        if isinstance(value, str):
            # 1) 纯引用快捷路径（整个值就是 {{step_N}} 或 {{step_N.path}}）：保留原始类型
            #    否则 list/dict 会被 str() 成字符串，下游工具无法使用
            pure_whole = re.fullmatch(r'\{\{step_(\d+)\}\}', value.strip())
            pure_field = re.fullmatch(r'\{\{step_(\d+)\.([^}]+)\}\}', value.strip())
            if pure_whole:
                step_idx = int(pure_whole.group(1))
                if step_idx < current_idx and step_idx in step_outputs:
                    logger.info(f"[Executor] 解析参数(整体保留): {{{{step_{step_idx}}}}} -> {type(step_outputs[step_idx]).__name__}")
                    resolved[key] = step_outputs[step_idx]
                    continue
                resolved[key] = value
                continue
            if pure_field:
                step_idx, field_path = int(pure_field.group(1)), pure_field.group(2)
                if step_idx < current_idx and step_idx in step_outputs:
                    field_value = _get_nested_field(step_outputs[step_idx], field_path)
                    if field_value is not None:
                        logger.info(f"[Executor] 解析参数(保留类型): {{{{step_{step_idx}.{field_path}}}}} -> {type(field_value).__name__}")
                        resolved[key] = field_value
                        continue
                resolved[key] = value
                continue

            # 2) 字符串拼接路径（如 "今天{{step_0.count}}起"）
            matches = re.findall(r'\{\{step_(\d+)\.([^}]+)\}\}', value)
            if matches:
                resolved_value = value
                for step_idx_str, field_path in matches:
                    step_idx = int(step_idx_str)

                    if step_idx >= current_idx:
                        logger.warning(f"[Executor] 步骤 {current_idx} 引用了未来步骤 {step_idx}，跳过")
                        continue

                    if step_idx not in step_outputs:
                        logger.warning(f"[Executor] 步骤 {step_idx} 输出不存在，无法解析 {{{{step_{step_idx}.{field_path}}}}}")
                        continue

                    # 支持嵌套字段访问，如 step_0.data.camera_id
                    field_value = _get_nested_field(step_outputs[step_idx], field_path)

                    if field_value is not None:
                        placeholder = f"{{{{step_{step_idx}.{field_path}}}}}"
                        resolved_value = resolved_value.replace(placeholder, str(field_value))
                        logger.info(f"[Executor] 解析参数: {placeholder} -> {field_value}")

                resolved[key] = resolved_value
            else:
                resolved[key] = value
        elif isinstance(value, dict):
            resolved[key] = _resolve_step_references(value, step_outputs, current_idx)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_step_references(item, step_outputs, current_idx) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            resolved[key] = value

    return resolved


def _get_nested_field(data: Any, field_path: str) -> Any:
    """
    从嵌套字典中获取字段值，支持点号分隔的路径

    例如：_get_nested_field({"data": {"camera_id": "CAM-001"}}, "data.camera_id") -> "CAM-001"
    """
    if not isinstance(data, dict):
        return None

    parts = field_path.split(".")
    current = data

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None

    return current


def _analyze_events(events: list) -> dict:
    """
    对 AI 告警事件列表进行多维度统计分析

    返回包含以下维度的统计结果：
    - 时间跨度（最早/最晚/跨度）
    - 告警类型分布（event_type / event_name）
    - 告警等级分布（level: red/orange/yellow/blue）
    - 复核状态分布（review_status）
    - 摄像头分布（camera_name top 10）
    - 设备分布（device_name top 10）
    - VLM 判断结果分布（llm_result）
    - 时段分布（按小时）
    """
    from collections import Counter
    from datetime import datetime as dt

    if not events:
        return {}

    stats = {
        'total': len(events),
        'time_range': {},
        'event_types': Counter(),
        'event_names': Counter(),
        'levels': Counter(),
        'review_status': Counter(),
        'cameras': Counter(),
        'devices': Counter(),
        'llm_verdicts': Counter(),
        'hours': Counter(),
        'dates': Counter(),
    }

    # 时间跨度
    times = []
    for e in events:
        # 类型分布
        stats['event_types'][e.get('event_type', 'unknown')] += 1
        stats['event_names'][e.get('event_name', e.get('event_type', 'unknown'))] += 1

        # 等级（展示用中文标签）
        from skills.ai_labels import level_label as _level_label
        stats['levels'][_level_label(e.get('level'))] += 1

        # 复核状态（与 KSIpms eventReviewStatusLabel 一致）
        from skills.ai_labels import review_status_label as _rs_label
        rs = e.get('review_status', 'unknown')
        stats['review_status'][_rs_label(rs) if rs not in (None, '', 'unknown') else 'unknown'] += 1

        # 摄像头/设备
        if cam := e.get('camera_name'):
            stats['cameras'][cam] += 1
        if dev := e.get('device_name'):
            stats['devices'][dev] += 1

        # VLM 判断
        llm = e.get('llm_result', {})
        if isinstance(llm, dict):
            verdict = llm.get('result', '未判定')
            stats['llm_verdicts'][verdict] += 1

        # 时间分布
        created_at = e.get('created_at', '')
        if created_at:
            try:
                t = dt.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                times.append(t)
                stats['hours'][t.hour] += 1
                stats['dates'][created_at[:10]] += 1
            except (ValueError, TypeError):
                pass

    # 时间跨度
    if times:
        stats['time_range'] = {
            'earliest': min(times).strftime('%Y-%m-%d %H:%M:%S'),
            'latest': max(times).strftime('%Y-%m-%d %H:%M:%S'),
            'span_hours': round((max(times) - min(times)).total_seconds() / 3600, 1),
        }

    return stats


def _generate_event_summary_chart(stats: dict) -> str | None:
    """
    根据统计结果生成可视化图表（多子图：类型分布 + 等级 + 时段 + Top摄像头）

    返回 base64 PNG 字符串，失败返回 None
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import io
        import base64

        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle(f"AI 告警分析摘要（共 {stats.get('total', 0)} 条）", fontsize=14, fontweight='bold')

        # 子图1: 告警类型分布（柱状图，Top 10）
        ax1 = axes[0, 0]
        top_types = stats['event_names'].most_common(10)
        if top_types:
            names = [t[0][:10] for t in top_types]  # 截断长名称
            counts = [t[1] for t in top_types]
            colors_t = plt.cm.Set3.colors[:len(names)]
            ax1.barh(names, counts, color=colors_t)
            ax1.set_title('告警类型 Top 10', fontsize=11)
            ax1.set_xlabel('数量')
            for i, v in enumerate(counts):
                ax1.text(v, i, f' {v}', va='center', fontsize=9)
        else:
            ax1.text(0.5, 0.5, '无类型数据', ha='center', va='center', transform=ax1.transAxes)

        # 子图2: 告警等级分布（饼图）
        ax2 = axes[0, 1]
        level_data = stats['levels']
        if level_data:
            from skills.ai_labels import LEVEL_LABEL
            level_color_map = {'red': '#dc3545', 'orange': '#fd7e14', 'yellow': '#ffc107', 'blue': '#0d6efd', 'unknown': '#6c757d'}
            # 反向映射：中文标签 -> 颜色
            cn_to_color = {v: level_color_map.get(k, '#6c757d') for k, v in LEVEL_LABEL.items()}
            labels = list(level_data.keys())
            sizes = list(level_data.values())
            colors_l = [cn_to_color.get(l, level_color_map.get(l, '#6c757d')) for l in labels]
            ax2.pie(sizes, labels=labels, autopct='%1.1f%%', colors=colors_l, startangle=90)
            ax2.set_title('告警等级分布', fontsize=11)
        else:
            ax2.text(0.5, 0.5, '无等级数据', ha='center', va='center', transform=ax2.transAxes)

        # 子图3: 时段分布（按小时）
        ax3 = axes[1, 0]
        hour_data = stats['hours']
        if hour_data:
            hours = sorted(hour_data.keys())
            counts = [hour_data[h] for h in hours]
            ax3.bar([f'{h:02d}时' for h in hours], counts, color='#0d6efd')
            ax3.set_title('告警时段分布', fontsize=11)
            ax3.set_xlabel('小时')
            ax3.set_ylabel('数量')
            ax3.tick_params(axis='x', rotation=45, labelsize=8)
        else:
            ax3.text(0.5, 0.5, '无时段数据', ha='center', va='center', transform=ax3.transAxes)

        # 子图4: Top 摄像头
        ax4 = axes[1, 1]
        top_cams = stats['cameras'].most_common(8)
        if top_cams:
            names = [c[0][:15] for c in top_cams]
            counts = [c[1] for c in top_cams]
            ax4.barh(names, counts, color='#20c997')
            ax4.set_title('Top 8 高发摄像头', fontsize=11)
            ax4.set_xlabel('数量')
            for i, v in enumerate(counts):
                ax4.text(v, i, f' {v}', va='center', fontsize=9)
        else:
            ax4.text(0.5, 0.5, '无摄像头数据', ha='center', va='center', transform=ax4.transAxes)

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=90, bbox_inches='tight')
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[Formatter] 图表生成失败: {e}")
        return None


def _generate_aggregate_chart(agg_data: list, group_label: str, total: int) -> str | None:
    """为 aggregate_alarms 的聚合结果生成柱状图，返回 base64 PNG（失败返回 None）"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import io
        import base64

        items = agg_data[:15]
        if not items:
            return None
        labels = [str(d.get('key', ''))[:12] for d in items]
        counts = [d.get('count', 0) for d in items]

        fig, ax = plt.subplots(figsize=(10, 6))
        colors = plt.cm.Set3.colors[:len(labels)] if len(labels) <= 12 else None
        bars = ax.bar(labels, counts, color=colors)
        ax.set_title(f"{group_label}分布（共 {total} 条）", fontsize=13, fontweight='bold')
        ax.set_ylabel('告警数量')
        ax.tick_params(axis='x', rotation=30, labelsize=9)
        # 柱顶标注数值
        for bar, c in zip(bars, counts):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    str(c), ha='center', va='bottom', fontsize=9)

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=90, bbox_inches='tight')
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[Formatter] 聚合图表生成失败: {e}")
        return None


# MCP 分页列表类工具：走结构化摘要路径时原先只输出「执行完成」，需专用格式化
_LIST_TOOL_SPECS: dict[str, dict] = {
    "video_device_list": {
        "items_key": "devices",
        "label": "视频设备",
        "name_keys": ("device_name", "deviceName", "name"),
    },
    "record_channel_list": {
        "items_key": "channels",
        "label": "录像通道",
        "name_keys": ("device_name", "deviceName", "channel_name", "channelName"),
    },
    "record_plan_list": {
        "items_key": "plans",
        "label": "录像计划",
        "name_keys": ("plan_name", "planName", "name"),
    },
    "ai_device_list": {
        "items_key": "devices",
        "label": "AI 分析设备",
        "name_keys": ("device_name", "deviceName", "name", "keywords"),
    },
    "ai_camera_list": {
        "items_key": "cameras",
        "label": "AI 摄像机",
        "name_keys": ("real_name", "realName", "camera_name", "cameraName", "name"),
    },
    "ai_task_list": {
        "items_key": "tasks",
        "label": "AI 分析任务",
        "name_keys": ("name", "code", "camera_name", "cameraName"),
    },
    "compress_task_list": {
        "items_key": "tasks",
        "label": "压缩任务",
        "name_keys": ("task_name", "taskName", "name", "device_name", "deviceName"),
    },
    "compress_device_list": {
        "items_key": "devices",
        "label": "压缩设备",
        "name_keys": ("device_name", "deviceName", "name"),
    },
}


def _item_display_name(item: dict, name_keys: tuple[str, ...]) -> str:
    for key in name_keys:
        if val := item.get(key):
            return str(val)
    if uid := item.get("uuid"):
        return str(uid)
    return "未知"


def _format_simple_list_tool_response(user_message: str, tool_result: dict) -> str | None:
    """格式化 MCP 分页列表类工具结果（数量统计 + 简要列表）。"""
    tool_name = tool_result.get("tool")
    spec = _LIST_TOOL_SPECS.get(tool_name or "")
    if not spec:
        return None

    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    items_key = spec["items_key"]
    label = spec["label"]
    name_keys = spec["name_keys"]

    items = result.get(items_key) or []
    if not isinstance(items, list):
        items = []

    summary = result.get(f"{items_key}_summary") or {}
    total = result.get("total")
    if total is None and summary:
        total = summary.get("total_count")
    if total is None:
        total = len(items)
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = len(items)

    showing = len(items)
    truncated = int(summary.get("truncated") or 0) if summary else max(0, total - showing)

    lines: list[str] = [f"平台共有 **{total}** 个{label}。"]
    if total == 0:
        lines.append("\n当前无匹配记录。")
        return "\n".join(lines)

    want_list = not any(k in (user_message or "") for k in ("多少", "几个", "数量", "总数", "共有"))
    want_list = want_list or showing <= 15 or truncated > 0

    if want_list and showing > 0:
        title = f"\n### {label}列表"
        if truncated > 0:
            title += f"（展示前 {showing} 条，共 {total} 条）"
        lines.append(title)
        for i, raw in enumerate(items[:15], 1):
            if not isinstance(raw, dict):
                continue
            name = _item_display_name(raw, name_keys)
            ip = raw.get("device_ip") or raw.get("deviceIp")
            status = raw.get("status")
            line = f"{i}. **{name}**"
            if ip:
                line += f"（{ip}）"
            if status is not None and str(status) != "":
                line += f" — 状态 {status}"
            lines.append(line)
        if truncated > 0:
            lines.append(f"\n> 另有 {truncated} 条未展示，可缩小关键词或分页查询。")

    return "\n".join(lines)


def _fmt_time_ms(ms) -> str:
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(ms)


def _extract_record_segments(calendar: dict) -> list:
    if not isinstance(calendar, dict):
        return []
    data = calendar.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _format_record_segments_lines(segments: list, limit: int = 20) -> list[str]:
    lines: list[str] = []
    for i, seg in enumerate(segments[:limit], 1):
        start = seg.get("startTime") or seg.get("start_time")
        end = seg.get("endTime") or seg.get("end_time")
        dur = seg.get("time") or seg.get("duration") or "-"
        rec = "（录制中）" if seg.get("recording") else ""
        lines.append(f"{i}. {_fmt_time_ms(start)} ~ {_fmt_time_ms(end)}  时长 {dur}s{rec}")
    if len(segments) > limit:
        lines.append(f"\n> 另有 {len(segments) - limit} 条片段未展示")
    return lines


def _user_wants_live_preview(msg: str) -> bool:
    msg = msg or ""
    if any(k in msg for k in ("录像", "回放", "片段", "日历")):
        return False
    if any(k in msg for k in ("直播", "预览", "实时")):
        return True
    if "播放" in msg and any(k in msg for k in ("视频", "直播", "预览", "设备", "相机", "摄像机")):
        return True
    if "打开" in msg and any(k in msg for k in ("直播", "预览", "视频", "设备", "相机", "摄像机")):
        return True
    return False


def _build_live_meta_from_result(result: dict, dev_name: str) -> dict:
    channel = result.get("channel") or {}
    return {
        "title": _format_device_channel_title(result, dev_name),
        "channelUuid": result.get("channel_uuid") or channel.get("uuid") or channel.get("channelUuid") or "",
        "app": result.get("app") or channel.get("app") or "",
        "stream": result.get("stream") or channel.get("stream") or channel.get("uuid") or "",
        "httpFlv": result.get("live_http_flv") or result.get("http_flv") or "",
        "wsFlv": result.get("live_ws_flv") or result.get("ws_flv") or "",
    }


def _format_device_channel_title(result: dict, default_name: str = "摄像机") -> str:
    device = result.get("device") or {}
    channel = result.get("channel") or {}
    dev_name = (
        device.get("device_name") or device.get("deviceName")
        or result.get("camera_name") or default_name
    )
    ch_name = channel.get("channelName") or channel.get("channel_name") or ""
    if ch_name and ch_name not in dev_name:
        return f"{dev_name}（{ch_name}）"
    return dev_name


def _append_live_preview_block(lines: list[str], live_meta: dict) -> None:
    if not (live_meta.get("httpFlv") or live_meta.get("wsFlv")):
        return
    lines.append(
        "\n<!-- xiaoke-live:"
        + json.dumps(live_meta, ensure_ascii=False)
        + " -->"
    )
    lines.append("\n点击下方「打开直播」按钮预览实时画面。")


def _format_video_camera_overview_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    if not result.get("matched"):
        return result.get("message") or f"未找到摄像机「{result.get('camera_name', '')}」"

    device = result.get("device") or {}
    channel = result.get("channel") or {}
    dev_name = (
        device.get("device_name") or device.get("deviceName")
        or result.get("camera_name") or "未知设备"
    )
    dev_ip = device.get("device_ip") or device.get("deviceIp") or ""
    ch_name = channel.get("channelName") or channel.get("channel_name") or ""

    focus_record = any(k in (user_message or "") for k in ("录像", "回放", "片段", "日历"))

    lines: list[str] = []
    if focus_record:
        lines.append(f"## {dev_name} — 今日录像")
    else:
        lines.append(f"## {dev_name} — 设备与录像概览")

    if dev_ip:
        lines.append(f"**设备 IP**：{dev_ip}")
    if ch_name:
        lines.append(f"**通道**：{ch_name}")

    rs_label = result.get("record_status_label")
    if rs_label:
        lines.append(f"**录像状态**：{rs_label}")

    cal = result.get("record_calendar_today") or {}
    segments = _extract_record_segments(cal)
    seg_count = result.get("record_segment_count_today")
    if seg_count is None:
        seg_count = len(segments)
    has_today = result.get("has_record_today")

    if err := result.get("record_calendar_error"):
        lines.append(f"\n⚠️ 今日录像查询失败：{err}")
    elif has_today or (seg_count and int(seg_count) > 0):
        lines.append(f"\n### 今日录像片段（共 **{seg_count}** 条）")
        lines.extend(_format_record_segments_lines(segments))
    elif result.get("recording_in_progress"):
        lines.append("\n今日录像正在写入中，片段可能尚未完整落盘。")
    else:
        lines.append("\n今日暂无录像片段记录。")

    if flv := result.get("live_http_flv"):
        lines.append(f"\n**直播 FLV**：`{flv}`")
    if pb := result.get("playback_http_flv"):
        lines.append(f"**回放 FLV**：`{pb}`")

    if result.get("compress_enabled"):
        tasks = result.get("compress_tasks") or []
        lines.append(f"\n**视频压缩**：已启用（关联 {len(tasks)} 个任务）")

    if _user_wants_live_preview(user_message) and result.get("live_http_flv"):
        live_meta = _build_live_meta_from_result(result, dev_name)
        channel = result.get("channel") or {}
        if not live_meta.get("app"):
            live_meta["app"] = channel.get("app") or ""
        if not live_meta.get("stream"):
            live_meta["stream"] = channel.get("stream") or ""
        _append_live_preview_block(lines, live_meta)

    return "\n".join(lines)


def _format_record_day_calendar_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    date = result.get("date") or "今天"
    channel_uuid = result.get("channel_uuid") or ""
    cal = result.get("calendar") or {}
    segments = _extract_record_segments(cal)
    count = result.get("segment_count")
    if count is None:
        count = len(segments)

    lines = [f"## 录像日历（{date}）"]
    if channel_uuid:
        lines.append(f"**通道 UUID**：{channel_uuid}")
    lines.append(f"**片段数**：**{count}** 条")

    if segments:
        lines.append("\n### 片段列表")
        lines.extend(_format_record_segments_lines(segments))
    elif result.get("recording_in_progress"):
        lines.append("\n该日录像正在写入中。")
    else:
        lines.append("\n该日暂无录像片段。")
    return "\n".join(lines)


def _format_video_record_segments_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    segments = result.get("segments") or result.get("data") or []
    if not isinstance(segments, list):
        segments = []
    total = result.get("total")
    if total is None:
        total = len(segments)

    lines = [f"## 录像片段查询结果", f"共 **{total}** 条片段"]
    if segments:
        lines.append("\n### 片段列表")
        lines.extend(_format_record_segments_lines(segments))
    else:
        lines.append("\n该时间范围内暂无录像片段。")
    return "\n".join(lines)


def _format_video_play_record_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    if not result.get("matched"):
        return result.get("message") or "录像回放启动失败，请确认该时间点是否有录像。"

    title = _format_device_channel_title(result, "摄像机")
    play_at = result.get("play_at") or ""
    channel = result.get("channel") or {}
    ch_name = channel.get("channelName") or channel.get("channel_name") or ""

    lines = [
        f"## {title} 录像回放",
        f"**起始时间**：{play_at}",
    ]
    if ch_name:
        lines.append(f"**通道**：{ch_name}")
    seg_count = result.get("record_segment_count")
    if seg_count is not None:
        lines.append(f"**当日录像片段数**：{seg_count}")

    playback_meta = {
        "title": title,
        "playAtMs": result.get("play_at_ms"),
        "channelUuid": result.get("channel_uuid") or "",
        "recordType": int(result.get("record_type") or 1),
        "playDate": result.get("play_date") or "",
        "timeSegments": result.get("time_segments") or [],
        "speed": int(result.get("speed") or 1),
    }
    if playback_meta["channelUuid"]:
        lines.append(
            "\n<!-- xiaoke-playback:"
            + json.dumps(playback_meta, ensure_ascii=False)
            + " -->"
        )
        lines.append("\n点击下方「播放录像」按钮打开播放器，可在时间轴上选点、快进或切换片段。")
    else:
        lines.append("\n未获取到有效通道，请检查摄像机名称。")

    return "\n".join(lines)


def _strip_om_route_prefix(path: str) -> str:
    """去掉 OM/静态资源路径中多余的 nginx 路由前缀（如 main/），避免前端 VITE_API 重复拼接。"""
    p = (path or "").strip().lstrip("/")
    if p.startswith("main/ai_data/"):
        return p[5:]
    if p.startswith("main/") and "ai_data/" in p:
        idx = p.find("ai_data/")
        return p[idx:]
    return p


def _normalize_event_video_path(video_path: str) -> str:
    """与 KSIpms AiHandleDialog.buildEventVideoUrl 一致：补全 ai_data/event_video 前缀。"""
    path = (video_path or "").strip()
    if not path or path.startswith(("http://", "https://")):
        return path
    normalized = _strip_om_route_prefix(path)
    if normalized.startswith("ai_data/"):
        return normalized
    return f"ai_data/event_video/{normalized}"


def _normalize_event_image_path(img_path: str) -> str:
    path = (img_path or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return _strip_om_route_prefix(path)


def _format_llm_result_line(event: dict) -> str | None:
    llm = event.get("llm_result")
    if not llm:
        return None
    if isinstance(llm, str):
        return f"**AI 判断结果**：{llm}"
    if not isinstance(llm, dict):
        return None
    result = llm.get("result") or llm.get("verdict") or llm.get("label")
    desc = llm.get("description") or llm.get("reason") or llm.get("reasoning") or ""
    conf = llm.get("confidence")
    parts = []
    if result is not None:
        parts.append(str(result))
    if desc:
        parts.append(str(desc))
    line = "，".join(parts) if parts else "—"
    if conf is not None:
        try:
            conf_str = f"{float(conf):.6f}".rstrip("0").rstrip(".")
            line += f"（AI 置信度 {conf_str}）"
        except (TypeError, ValueError):
            line += f"（AI 置信度 {conf}）"
    return f"**AI 判断结果**：{line}"


def _append_alarm_media_block(lines: list[str], event: dict, title_prefix: str = "") -> None:
    """追加告警图片/视频预览链接（xiaoke-alarm-media 元数据 + Markdown 链接）。"""
    media_meta: dict[str, dict] = {}
    img_path = _normalize_event_image_path(event.get("img_path") or "")
    video_path = _normalize_event_video_path(event.get("video_path") or "")

    if img_path:
        media_meta["image"] = {
            "type": "image",
            "path": img_path,
            "title": f"{title_prefix}告警图片".strip(),
        }
    if video_path:
        media_meta["video"] = {
            "type": "video",
            "path": video_path,
            "title": f"{title_prefix}告警视频".strip(),
        }
    if not media_meta:
        return

    link_parts = []
    if "image" in media_meta:
        link_parts.append("[查看告警图片](xiaoke-alarm-media:image)")
    if "video" in media_meta:
        link_parts.append("[查看告警视频](xiaoke-alarm-media:video)")
    if link_parts:
        lines.append(f"**告警媒体**：{' · '.join(link_parts)}")
    lines.append(
        "\n<!-- xiaoke-alarm-media:"
        + json.dumps(media_meta, ensure_ascii=False)
        + " -->"
    )


def _format_single_ai_event_lines(event: dict, *, heading: str | None = None) -> list[str]:
    """格式化单条 AI 告警（字段标签与 KSIpms 前端一致）。"""
    from skills.ai_labels import level_label, review_status_label, workflow_status_label

    lines: list[str] = []
    if heading:
        lines.append(heading)

    event_name = event.get("event_name") or event.get("event_type") or "未知类型"
    event_type = event.get("event_type") or ""
    if event_type and event_type != event_name:
        lines.append(f"**告警类型**：{event_name}（事件类型：{event_type}）")
    else:
        lines.append(f"**告警类型**：{event_name}")

    if created_at := event.get("created_at"):
        lines.append(f"**告警时间**：{created_at}")

    level = event.get("level")
    if level:
        lines.append(f"**告警级别**：{level_label(level)}（{level}）")

    device_name = event.get("device_name") or "未知设备"
    device_uuid = event.get("device_uuid") or ""
    if device_uuid:
        lines.append(f"**告警设备**：{device_name}（设备 UUID: {device_uuid}）")
    else:
        lines.append(f"**告警设备**：{device_name}")

    camera_name = event.get("camera_name") or ""
    camera_uuid = event.get("camera_uuid") or ""
    if camera_name:
        if camera_uuid:
            lines.append(f"**摄像头名称**：{camera_name}（摄像头 UUID: {camera_uuid}）")
        else:
            lines.append(f"**摄像头名称**：{camera_name}")

    if llm_line := _format_llm_result_line(event):
        lines.append(llm_line)

    wf = workflow_status_label(event.get("status"))
    rs = review_status_label(event.get("review_status"))
    lines.append(f"**处理状态**：{wf}")
    if event.get("review_status") is not None:
        lines.append(f"**复核结果**：{rs}（review_status: {event.get('review_status')}）")

    title_prefix = event_name if event_name != "未知类型" else ""
    _append_alarm_media_block(lines, event, title_prefix=title_prefix)
    return lines


def _format_event_stats_summary(events: list, total: int, user_message: str) -> str:
    """将 ai_event_list 结果格式化为统计报告（按摄像头/时段分布）。"""
    from collections import Counter

    msg = user_message or ""
    type_name = ""
    if events and isinstance(events[0], dict):
        type_name = events[0].get("event_name") or events[0].get("event_type") or ""

    lines = ["## 📊 告警统计结果"]
    if type_name:
        lines.append(f"**告警类型**：{type_name}")
    time_hint = "今天" if "今天" in msg else ("昨天" if "昨天" in msg else "")
    if time_hint:
        lines.append(f"**统计范围**：{time_hint}")
    lines.append(f"**合计**：共 **{total}** 条\n")

    if total > len(events):
        lines.append(f"> 以下分布基于已拉取的 {len(events)} 条样本\n")

    by_camera = Counter(
        (e.get("camera_name") or "未知摄像头") for e in events if isinstance(e, dict)
    )
    if by_camera:
        lines.append("### 按摄像头分布")
        for cam, cnt in by_camera.most_common():
            pct = cnt / total * 100 if total else 0
            bar = "█" * max(1, int(pct / 5))
            lines.append(f"- **{cam}**：{cnt} 条 ({pct:.1f}%) {bar}")
        lines.append("")

    by_hour = Counter()
    for e in events:
        if not isinstance(e, dict):
            continue
        ca = e.get("created_at") or ""
        if len(ca) >= 13:
            try:
                by_hour[int(ca[11:13])] += 1
            except ValueError:
                pass
    if by_hour:
        lines.append("### 时段分布（按小时）")
        for h in sorted(by_hour):
            lines.append(f"- {h:02d}:00–{h:02d}:59：{by_hour[h]} 条")

    return "\n".join(lines)


def _format_ai_event_list_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    events = result.get("events") or []
    if not isinstance(events, list) or not events:
        return None

    total = result.get("total", len(events))
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = len(events)

    msg = user_message or ""
    want_stats = _user_wants_stats(msg)
    # 统计类：输出聚合报告（不受 5 条上限限制）
    if want_stats and not any(k in msg for k in ("最新", "最近一条", "最后一条", "详情", "明细")):
        return _format_event_stats_summary(events, total, user_message)

    # 大量非统计类数据走摘要统计路径
    if len(events) > 5:
        return None

    want_latest = any(k in msg for k in ("最新", "最近一条", "最后一条", "最新一条"))
    want_detail = want_latest or len(events) == 1 or any(
        k in msg for k in ("详情", "明细", "哪一条", "那条", "这条")
    )

    if want_detail and len(events) >= 1:
        event = events[0]
        if not isinstance(event, dict):
            return None
        heading = "## 最新 AI 告警详情" if want_latest and total > 1 else "## AI 告警详情"
        if total > len(events):
            heading += f"\n（平台共 {total} 条，展示最新 1 条）"
        return "\n".join(_format_single_ai_event_lines(event, heading=heading))

    lines = [f"## AI 告警查询结果", f"共 **{total}** 条，展示 **{len(events)}** 条：\n"]
    for i, event in enumerate(events[:5], 1):
        if not isinstance(event, dict):
            continue
        from skills.ai_labels import level_label, review_status_label, workflow_status_label
        name = event.get("event_name") or event.get("event_type") or "未知"
        created = event.get("created_at") or "—"
        cam = event.get("camera_name") or "—"
        lines.append(
            f"{i}. **{name}** · {created} · {cam} · "
            f"{level_label(event.get('level'))} · 处理:{workflow_status_label(event.get('status'))} · "
            f"复核:{review_status_label(event.get('review_status'))}"
        )
    return "\n".join(lines)


def _format_ai_event_detail_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None
    event = result.get("event")
    if not isinstance(event, dict) or not event:
        return result.get("message") or "未找到该告警详情"
    return "\n".join(_format_single_ai_event_lines(event, heading="## AI 告警详情"))


def _format_video_live_play_response(user_message: str, tool_result: dict) -> str | None:
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return None

    if not result.get("matched"):
        return result.get("message") or f"未找到摄像机「{result.get('camera_name', '')}」"

    title = _format_device_channel_title(result, "摄像机")
    channel = result.get("channel") or {}
    ch_name = channel.get("channelName") or channel.get("channel_name") or ""

    lines = [f"## {title} 实时预览"]
    if ch_name:
        lines.append(f"**通道**：{ch_name}")
    live_meta = _build_live_meta_from_result(result, title)
    _append_live_preview_block(lines, live_meta)
    if not (live_meta.get("httpFlv") or live_meta.get("wsFlv")):
        lines.append("\n未获取到有效直播地址，请确认设备在线且通道已推流。")

    return "\n".join(lines)


_STRUCTURED_MCP_FORMATTERS = {
    "ai_event_list": _format_ai_event_list_response,
    "ai_event_detail": _format_ai_event_detail_response,
    "video_camera_overview": _format_video_camera_overview_response,
    "record_day_calendar": _format_record_day_calendar_response,
    "video_record_find_segments": _format_video_record_segments_response,
    "video_play_record": _format_video_play_record_response,
    "video_record_start_playback": _format_video_play_record_response,
    "video_live_play": _format_video_live_play_response,
}


def _format_structured_mcp_response(user_message: str, tool_result: dict) -> str | None:
    fn = _STRUCTURED_MCP_FORMATTERS.get(tool_result.get("tool") or "")
    if fn is None:
        return None
    return fn(user_message, tool_result)


def _generate_summary_response(user_message: str, tool_results: list) -> dict:
    """
    当数据量过大时，对原始数据做多维度统计分析，生成结构化摘要 + 可视化图表

    设计目标：
    - 不再返回简陋的"查询到 N 条"，而是给出有价值的洞察
    - 多维度统计：类型/等级/状态/时段/Top摄像头/Top设备/VLM判断
    - 自动生成可视化图表（柱状图+饼图组合）
    - 友好的中文报告格式
    """
    response_parts = []
    chart_image_base64 = None

    # 🔧 修复图表类型覆盖问题：优先使用用户在 visualize_alarms 中指定的图表
    # 检查是否已有 visualize_alarms 的结果（包含用户指定的 chart_type）
    user_chart = None
    for r in tool_results:
        if r.get("success") and r.get("tool") == "visualize_alarms":
            result = r.get("result", {})
            if "image_base64" in result:
                user_chart = {
                    "image": result["image_base64"],
                    "type": result.get("chart_type", "bar"),
                    "title": result.get("title", "告警统计图表"),
                }
                chart_image_base64 = result["image_base64"]
                break

    for r in tool_results:
        if not r.get("success"):
            response_parts.append(f"⚠️ 工具 {r['tool']} 执行失败: {r.get('error', '未知错误')}")
            continue

        tool_name = r["tool"]
        result = r.get("result", {})

        # 处理告警事件列表
        if tool_name == "ai_event_list" or 'events' in result:
            # 关键改进：使用工具调用时的 ORIGINAL 数据做统计（如果可用）
            # 否则基于截断后的样本做统计（提示用户）
            events_full = r.get("_raw_events") or result.get('events', [])
            total_count = result.get('total', len(events_full))

            # 如果 _strip_large_fields 已经截断，使用 events_summary 中的总数
            summary_info = result.get('events_summary', {})
            if summary_info:
                total_count = summary_info.get('total_count', total_count)

            if not events_full:
                response_parts.append("❌ 未查询到符合条件的 AI 告警")
                continue

            # 多维度统计
            stats = _analyze_events(events_full)

            # 生成可视化图表
            chart_b64 = _generate_event_summary_chart(stats)
            if chart_b64:
                chart_image_base64 = chart_b64

            # ===== 生成结构化报告 =====
            report = []
            report.append(f"## 📊 AI 告警分析报告\n")
            report.append(f"**查询结果**：共 **{total_count} 条**告警（基于 {stats['total']} 条样本统计）\n")

            # 时间跨度
            if tr := stats.get('time_range'):
                report.append(f"### ⏰ 时间跨度")
                report.append(f"- 最早：{tr['earliest']}")
                report.append(f"- 最晚：{tr['latest']}")
                report.append(f"- 跨度：约 {tr['span_hours']} 小时\n")

            # 告警类型分布
            if stats['event_names']:
                report.append(f"### 🚨 告警类型分布（Top 5）")
                for name, count in stats['event_names'].most_common(5):
                    pct = count / stats['total'] * 100
                    report.append(f"- **{name}**: {count} 条 ({pct:.1f}%)")
                report.append("")

            # 告警等级
            if stats['levels']:
                report.append(f"### ⚡ 告警等级")
                for level, count in stats['levels'].most_common():
                    pct = count / stats['total'] * 100
                    report.append(f"- **{level}**: {count} 条 ({pct:.1f}%)")
                report.append("")

            # 复核状态
            if stats['review_status']:
                report.append(f"### ✅ 复核状态")
                for status, count in stats['review_status'].most_common():
                    pct = count / stats['total'] * 100
                    report.append(f"- **{status}**: {count} 条 ({pct:.1f}%)")
                report.append("")

            # Top 摄像头
            if stats['cameras']:
                report.append(f"### 📹 高发摄像头（Top 5）")
                for cam, count in stats['cameras'].most_common(5):
                    report.append(f"- **{cam}**: {count} 条")
                report.append("")

            # Top 设备
            if stats['devices']:
                report.append(f"### 🖥️ 高发设备（Top 3）")
                for dev, count in stats['devices'].most_common(3):
                    report.append(f"- **{dev}**: {count} 条")
                report.append("")

            # VLM 判断结果
            if stats['llm_verdicts']:
                report.append(f"### 🤖 AI 智能判断")
                for verdict, count in stats['llm_verdicts'].most_common():
                    pct = count / stats['total'] * 100
                    report.append(f"- **{verdict}**: {count} 条 ({pct:.1f}%)")
                report.append("")

            # 时段分布关键洞察
            if stats['hours']:
                peak_hour, peak_count = stats['hours'].most_common(1)[0]
                report.append(f"### 📈 时段洞察")
                report.append(f"- 高发时段：**{peak_hour:02d}:00-{peak_hour+1:02d}:00**（{peak_count} 条）")
                report.append("")

            # 图表提示
            if chart_image_base64:
                report.append(f"### 📊 可视化图表")
                report.append(f"已生成多维度分析图表（含类型分布/等级/时段/Top摄像头）\n")

            response_parts.append("\n".join(report))

        elif tool_name == "aggregate_alarms":
            # aggregate_alarms 实际返回字段：data / group_by / total / platform_total / sampled
            agg_data = result.get('data', [])
            total = result.get('total', 0)
            platform_total = result.get('platform_total', total)
            group_by = result.get('group_by', 'event_name')
            sampled = result.get('sampled', False)

            group_label = {
                'event_name': '告警类型', 'event_type': '告警类型编码',
                'date': '日期', 'camera': '摄像头', 'level': '告警等级',
            }.get(group_by, group_by)

            if agg_data:
                report = [f"## 📊 告警统计报告\n"]
                if cam := result.get("camera_name"):
                    report.append(f"**筛选摄像机**：{cam}")
                report.append(f"**统计维度**：按{group_label}分组")
                if sampled:
                    report.append(f"**数据规模**：平台共 {platform_total} 条，本次统计 {total} 条（采样）\n")
                else:
                    report.append(f"**告警总数**：{total} 条（平台完整数据）\n")

                report.append(f"### 分布明细")
                from skills.ai_labels import level_label as _agg_level_label
                for item in agg_data[:15]:
                    key = item.get('key', 'unknown')
                    if group_by == 'level':
                        display_key = _agg_level_label(str(key))
                    else:
                        display_key = key
                    count = item.get('count', 0)
                    pct = count / total * 100 if total > 0 else 0
                    # 简易文本条形（直观展示占比）
                    bar = '█' * max(1, int(pct / 5))
                    report.append(f"- **{display_key}**：{count} 条 ({pct:.1f}%) {bar}")

                # 🔧 修复图表类型问题：只有在用户没用 visualize_alarms 时，才自己画柱状图
                # 否则用户指定的 pie/line 会被覆盖
                if not user_chart:
                    chart_b64 = _generate_aggregate_chart(agg_data, group_label, total)
                    if chart_b64:
                        chart_image_base64 = chart_b64
                        report.append(f"\n### 📊 可视化图表")
                        report.append(f"已生成「{group_label}分布」柱状图\n")
                else:
                    # 用户已经指定了图表类型（pie/line/bar），复述一下
                    report.append(f"\n### 📊 可视化图表")
                    chart_type_cn = {"bar": "柱状图", "line": "折线图", "pie": "饼图"}.get(
                        user_chart["type"], user_chart["type"]
                    )
                    report.append(f"已生成「{group_label}分布」{chart_type_cn}\n")

                response_parts.append("\n".join(report))
            else:
                cam = result.get("camera_name")
                et = resolve_event_type_from_text(user_message)
                type_label = _event_display_name(et) if et else ""
                if cam:
                    response_parts.append(f"❌ 「{cam}」在所选时间范围内未统计到 AI 告警，共 **0 条**")
                elif type_label:
                    response_parts.append(f"❌ 今天未统计到「{type_label}」告警，共 **0 条**")
                else:
                    response_parts.append("❌ 未统计到符合条件的告警数据")

        elif tool_name == "visualize_alarms":
            if 'image_base64' in result:
                response_parts.append("✅ 已生成可视化图表")
                if not chart_image_base64:
                    chart_image_base64 = result['image_base64']

        else:
            struct_text = _format_structured_mcp_response(user_message, r)
            if struct_text:
                response_parts.append(struct_text)
            else:
                list_text = _format_simple_list_tool_response(user_message, r)
                if list_text:
                    response_parts.append(list_text)
                else:
                    response_parts.append(f"✅ {tool_name} 执行完成")

    final_response = "\n\n---\n\n".join(response_parts)

    # 将图表直接内联到 markdown（Gradio Chatbot 支持 base64 图片渲染），
    # 不依赖 Web 层单独取 chart_image_base64 字段，确保"已生成图表"能真正显示。
    if chart_image_base64:
        # 🔧 修复图表不显示：chart_image_base64 有两种来源，前缀不一致——
        #   · visualize_alarms 返回的已带 "data:image/png;base64," 前缀
        #   · _generate_*_chart 返回的是裸 base64（无前缀）
        # 统一规范化，避免拼出 "data:image/png;base64,data:image/png;base64,..." 双前缀，
        # 双前缀会导致 Gradio 无法解析渲染（用户实测"回复中无图像"的根因）。
        if chart_image_base64.startswith("data:"):
            _img_src = chart_image_base64
        else:
            _img_src = f"data:image/png;base64,{chart_image_base64}"
        final_response += f"\n\n![统计图表]({_img_src})"

    return {
        "final_response": final_response,
        "chart_image_base64": chart_image_base64,  # 兼容其他调用方
    }


def _strip_large_fields(data, _max_str=800, _max_array_items=10):
    """递归剥离工具结果里的超大字段（如 base64 图片、大数组），避免撑爆 LLM 上下文。

    base64 图片等只保留占位摘要，formatter 只需知道"有一张图"即可。
    大数组（如告警列表）只保留前 N 条 + 统计摘要。

    Args:
        data: 待处理的数据
        _max_str: 字符串最大长度，超过则截断
        _max_array_items: 数组最大保留条数，超过则截断并添加摘要
    """
    BIG_KEYS = {"image_base64", "image", "snapshot_base64", "thumbnail"}
    ARRAY_KEYS = {"events", "devices", "users", "roles", "alarms", "video_clips"}  # 已知的大数组字段

    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in BIG_KEYS and isinstance(v, str):
                out[k] = f"<已生成图片, {len(v)} 字节, 省略内容>"
            elif isinstance(v, str) and len(v) > _max_str:
                out[k] = v[:_max_str] + f"...<截断, 共{len(v)}字符>"
            elif k in ARRAY_KEYS and isinstance(v, list) and len(v) > _max_array_items:
                # 大数组截断：保留前 N 条 + 摘要
                out[k] = [_strip_large_fields(x, _max_str, _max_array_items) for x in v[:_max_array_items]]
                out[f"{k}_summary"] = {
                    "total_count": len(v),
                    "showing": _max_array_items,
                    "truncated": len(v) - _max_array_items,
                    "note": f"数据量过大，仅显示前 {_max_array_items} 条，共 {len(v)} 条"
                }
            else:
                out[k] = _strip_large_fields(v, _max_str, _max_array_items)
        return out
    if isinstance(data, list):
        # 对于顶层或未命名的大数组，直接截断
        if len(data) > _max_array_items * 3:  # 阈值更高，避免误伤小数组
            return [_strip_large_fields(x, _max_str, _max_array_items) for x in data[:_max_array_items]] + \
                   [{"_truncated": f"省略 {len(data) - _max_array_items} 条，共 {len(data)} 条"}]
        return [_strip_large_fields(x, _max_str, _max_array_items) for x in data]
    return data


def formatter_node(state: AgentState) -> Dict[str, Any]:
    """
    格式化节点：汇总工具结果，生成最终响应

    使用 LLM 将工具结果格式化为自然语言回答。
    """
    logger.info("[Formatter] 格式化最终响应")

    tool_results = state.get("tool_results", [])
    user_message = state.get("user_message", "")

    if not tool_results:
        return {"final_response": "无结果"}

    # 纯对话：由 LLM 流式生成（简单问答、闲聊等）
    if (len(tool_results) == 1
            and tool_results[0].get("success")
            and tool_results[0].get("tool") == "stream_chat"):
        from utils.llm_pool import get_llm
        llm = get_llm(role="formatter", temperature=0.5)
        system_prompt = (
            "你是 KSIpms 综合管理平台的智能助手。"
            "请用简洁、准确、友好的中文回答用户问题。"
            "可介绍平台能力：查询 AI 告警、统计分析、规章制度检索、图像分析等。"
            "不要编造平台数据；若需查具体数据，提示用户使用明确的查询指令。"
        )
        user_prompt = user_message
        history_block = history_prompt_block(state.get("messages", []))
        if history_block:
            user_prompt = history_block + "\n# 本轮用户问题\n" + user_message
        emit_status("answering", "正在组织回答…")
        try:
            final_response = stream_llm(llm, [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        except Exception as e:
            logger.error(f"[Formatter] stream_chat 失败: {e}")
            final_response = "抱歉，暂时无法回答，请稍后重试。"
            emit_token_chunked(final_response)
        return {"final_response": final_response}

    # 固定文案 direct_response（预路由闲聊、平台不支持某类型等）
    if (len(tool_results) == 1
            and tool_results[0].get("success")
            and tool_results[0].get("tool") == "direct_response"):
        _text = tool_results[0]["result"].get("text", "")
        emit_status("answering", "正在组织回答…")
        emit_token_chunked(_text)
        return {"final_response": _text}

    # MCP 结构化/列表类工具：直接输出，避免误入告警摘要路径后只显示「执行完成」
    if len(tool_results) == 1 and tool_results[0].get("success"):
        _struct_text = _format_structured_mcp_response(user_message, tool_results[0])
        if _struct_text:
            emit_status("answering", "正在组织回答…")
            emit_token_chunked(_struct_text)
            return {"final_response": _struct_text}
        _list_text = _format_simple_list_tool_response(user_message, tool_results[0])
        if _list_text:
            emit_status("answering", "正在组织回答…")
            emit_token_chunked(_list_text)
            return {"final_response": _list_text}

    # 空结果守卫：ai_event_list 查到 0 条时，必须如实告知"无数据"，
    # 绝不能让下游 LLM 凭空编造无关告警（修复"打电话/跳舞也返回数据"的幻觉问题）。
    # H8 挂载点：行动后统一核对（H5 骨架默认放行，H8 实施时收敛现有守卫逻辑）
    if is_guard_enabled(CONFIG):
        for r in tool_results:
            if r.get("success") and r.get("tool"):
                guard_result = check_result(r.get("tool"), r.get("result", {}), CONFIG)
                log_guard_action(guard_result, context=f"formatter_node:check_result[{r.get('tool')}]")

                if not guard_result.allow:
                    # 命中空结果/错误：走确定性分支，不进 LLM
                    logger.info(f"[Formatter] 后置核对命中: {r.get('tool')} - {guard_result.reason}")
                    # H8 实施时会在这里添加确定性分支逻辑
                    # H5 阶段：护栏默认放行，原有守卫逻辑继续生效

    _ev_results = [
        r for r in tool_results
        if r.get("success") and r.get("tool") == "ai_event_list"
        and isinstance(r.get("result"), dict)
    ]
    if _ev_results and all(
        (r["result"].get("total", 0) == 0 and not r["result"].get("events"))
        for r in _ev_results
    ):
        # 复述本次实际使用的筛选条件，让用户确认确实查了对的东西
        filt = {}
        for r in _ev_results:
            for k in ("event_type", "time_start", "time_end", "level", "review_status"):
                if (v := state.get("plan", [{}])[0].get("args", {}).get(k)) is not None:
                    filt[k] = v

        # 关键区分：能走到 ai_event_list（而非 direct_response）说明 Planner 已确认
        # 该类型是"平台支持的算法类型"。因此 total==0 表示"平台支持但当前暂无此类
        # 告警记录"，而不是"平台不支持该类型"——绝不能把两者混为一谈。
        from skills.event_types import normalize_event_type
        et_raw = filt.get("event_type")
        et = normalize_event_type(et_raw) if et_raw else None
        has_time = bool(filt.get("time_start") or filt.get("time_end"))

        if et:
            type_label = _event_display_name(et)
            code_suffix = f"（{et}）" if type_label != et else ""
            if has_time:
                cond_desc = "在所选时间范围内"
                detail = "该类型告警在所选时间范围内暂无记录，可尝试去掉时间限制查询全部历史。"
            else:
                cond_desc = "（已查全部历史，无时间限制）"
                detail = ("平台**支持**「" + type_label + "」这类算法识别，"
                          "但数据库中当前**暂无**该类型的告警记录。")
            logger.info(f"[Formatter] ai_event_list 空结果：支持但无数据 event_type={et} time={has_time}")
            _text = (
                f"未查询到「{type_label}」{code_suffix}的告警记录{cond_desc}，共 **0 条**。\n\n"
                f"{detail}"
            )
            emit_token_chunked(_text)
            return {"final_response": _text}

        cond = "（无筛选条件，已查全量）" if not filt else "（筛选条件：" + ", ".join(f"{k}={v}" for k, v in filt.items()) + "）"
        logger.info(f"[Formatter] ai_event_list 返回空结果，输出无数据提示 {cond}")
        _text = (
            f"未在平台中查询到符合条件的 AI 告警{cond}，共 **0 条**。\n\n"
            f"该筛选条件下平台暂无告警记录，请确认筛选条件是否符合预期。"
        )
        emit_token_chunked(_text)
        return {"final_response": _text}

    # aggregate_alarms 空结果
    _agg_results = [
        r for r in tool_results
        if r.get("success") and r.get("tool") == "aggregate_alarms"
        and isinstance(r.get("result"), dict)
    ]
    if len(tool_results) == 1 and _agg_results:
        agg = _agg_results[0]["result"]
        agg_total = int(agg.get("total") or 0)
        if agg_total == 0 and not agg.get("data"):
            et = resolve_event_type_from_text(user_message)
            type_label = _event_display_name(et) if et else ""
            cam = agg.get("camera_name")
            if cam:
                _text = f"❌ 「{cam}」在所选时间范围内未统计到 AI 告警，共 **0 条**。"
            elif type_label:
                _text = f"❌ 今天未统计到「{type_label}」告警，共 **0 条**。"
            else:
                _text = "❌ 未统计到符合条件的告警数据。"
            emit_token_chunked(_text)
            return {"final_response": _text}

    # 构造工具结果摘要
    summary_parts = []
    has_large_data = False  # 标记是否有大数据量
    has_event_array = False  # 标记是否包含 events 数组（适合做多维度统计）
    has_aggregate = False  # 标记是否包含聚合统计结果（aggregate_alarms）

    for r in tool_results:
        if r.get("success"):
            tool_name = r["tool"]
            raw_result = r.get("result", {})
            result_data = _strip_large_fields(raw_result)

            # 检查是否有大数据量截断
            if any(k.endswith('_summary') for k in result_data.keys()):
                has_large_data = True

            # 检查是否包含 events 数组（无论是否截断，超过 5 条就走摘要路径）
            if isinstance(raw_result, dict) and isinstance(raw_result.get('events'), list):
                if len(raw_result['events']) > 5:
                    has_event_array = True

            # 检查是否为聚合统计结果（aggregate_alarms 返回 data + group_by）
            if tool_name == "aggregate_alarms" and isinstance(raw_result, dict) and raw_result.get('data'):
                has_aggregate = True

            summary_parts.append(f"[工具 {tool_name} 返回]\n{json.dumps(result_data, ensure_ascii=False, indent=2)}")
        else:
            summary_parts.append(f"[工具 {r['tool']} 执行失败: {r.get('error', '未知错误')}]")

    tools_summary = "\n\n".join(summary_parts)

    # 触发摘要路径的条件（任一满足即走结构化统计 + 可视化呈现）：
    # 1. 数据被 _strip_large_fields 截断
    # 2. events 数组超过 5 条
    # 3. 聚合统计结果（让聚合也有友好的结构化呈现 + 图表，而非 LLM 一句话）
    # 4. 工具结果 JSON 超过 4000 字符（保守阈值，远低于 8192 tokens）
    # 注：纯列表类 MCP 工具已在上方专用分支处理，不在此走告警摘要路径
    _single_list_tool = (
        len(tool_results) == 1
        and tool_results[0].get("success")
        and tool_results[0].get("tool") in _LIST_TOOL_SPECS
    )
    should_use_summary = (
        not _single_list_tool
        and (has_large_data or has_event_array or has_aggregate or len(tools_summary) > 4000)
    )
    if should_use_summary:
        logger.info(
            f"[Formatter] 触发结构化摘要路径 "
            f"(large_data={has_large_data}, event_array={has_event_array}, "
            f"aggregate={has_aggregate}, summary_len={len(tools_summary)})"
        )
        emit_status("summarizing", "正在统计分析并生成图表…")
        summary_out = _generate_summary_response(user_message, tool_results)
        _text = summary_out.get("final_response", "")
        if _text:
            emit_token_chunked(_text, chunk_size=8)
        return summary_out

    # 用 LLM 生成自然语言回答（使用客户端单例）
    from utils.llm_pool import get_llm
    llm = get_llm(role="formatter", temperature=0.3)

    # T10：统一回答骨架（对齐 LLM 路径到结构化路径的视觉风格）
    from graph.answer_skeleton import ANSWER_SKELETON_GUIDE

    system_prompt = """你是 KSIpms 综合管理平台的智能助手。根据用户问题和工具调用结果，用自然语言生成简洁清晰的回答。

# 真实平台关键字段速查（解读工具结果时使用）
- AI 事件: `events[]`，每项含 uuid / event_type / event_name / camera_name / created_at / img_path / video_path / level / status / review_status
  · level: red→特别重大 orange→重大 yellow→较大 blue→一般
  · status: unconfirmed/confirmed/finished/misinformation/ignore（处理状态）
  · review_status: 1=复核误报 2=复核告警 3=未复核 4=已复核
  · 告警图片/视频用 `[查看告警图片](xiaoke-alarm-media:image)` 链接 + `<!-- xiaoke-alarm-media:{...} -->` 元数据
- 视频设备: `devices[]`，每项含 uuid / device_name / device_ip / status
- 聚合统计: `data: [{key, count}]` + `total` + `platform_total`（若 sampled=True 提示是基于采样）
- VLM 复判: `verdict` (confirmed/rejected/uncertain) + `confidence` + `reasoning`
- 图表生成: `image_base64` 字段（已被剥离为占位符摘要，告诉用户"已生成图表"即可）

# 输出要求
1. 直接回答问题，不要重复用户问句
2. 中文回答，结构清晰；多条数据用列表/表格
3. 不编造，只基于工具返回的真实结果
4. 工具失败时，明确说明失败原因
5. 涉及数字（数量/置信度等）保留原值，不要四舍五入到整数

""" + ANSWER_SKELETON_GUIDE  # T10：追加统一骨架要求

    user_prompt = f"""用户问题：{user_message}

工具调用结果：
{tools_summary}

请根据以上信息生成回答。"""

    # 短期记忆：拼接本会话历史，便于回答时延续上下文（如"和刚才那次比"）。
    history_block = history_prompt_block(state.get("messages", []))
    if history_block:
        user_prompt = history_block + "\n" + user_prompt
    emit_status("answering", "正在组织回答…")
    try:
        # 流式逐字生成（stream_llm 在非流式请求下等价于一次性返回全文）
        final_response = stream_llm(llm, [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
    except Exception as e:
        logger.error(f"[Formatter] LLM 格式化失败: {e}")
        # 降级：简单拼接
        final_response = "\n\n".join(summary_parts)

    return {"final_response": final_response}


def should_continue(state: AgentState) -> str:
    """
    条件边：判断是否继续执行任务

    返回：
    - "execute" - 继续执行下一个任务
    - "format" - 所有任务完成，进入格式化
    """
    idx = state["current_task_idx"]
    plan = state["plan"]

    if idx >= len(plan):
        return "format"
    else:
        return "execute"


# ===================== 多模态入口分支（与综合管理平台聊天框对接） =====================
def route_by_modality(state: AgentState) -> str:
    """入口路由：根据是否带图，选择走 VLM 直接对话 还是 Plan-Execute 平台数据链路。

    设计语义（v1）：
        - 用户上传了图片 → 默认意图是"让模型看这张图"（ChatGPT 式体验），走 vlm_chat。
          MCP 工具针对的是平台数据库的告警，对用户上传的本地图片无意义；强行规划只会
          产生无关的工具调用与延迟，且 Planner 多模态化复杂度高，故 v1 不做。
        - 仅文本 → 走 Planner（保持原有真实平台对接全部能力不变）。

    返回值与下游图边的 key 对应：'vlm_chat' | 'planner'
    """
    images = state.get("images") or []
    if images:
        logger.info(f"[Router] 检测到 {len(images)} 张图，走 VLM 多模态对话")
        return "vlm_chat"
    return "planner"


def _normalize_image_url(img: str) -> str:
    """把任意输入归一化为 data URL：
    - 已经是 'data:image/...;base64,xxx' → 原样返回
    - 'http(s)://...' → 原样返回（OpenAI 兼容接口直接拉远端图）
    - 裸 base64 → 默认按 image/jpeg 包装
    """
    if not img:
        return img
    s = img.strip()
    if s.startswith("data:image") or s.startswith("http://") or s.startswith("https://"):
        return s
    return f"data:image/jpeg;base64,{s}"


def vlm_chat_node(state: AgentState) -> Dict[str, Any]:
    """多模态对话节点：把"图片 + 文本"直接交给 Qwen3-VL，跳过 Planner。

    用于：用户在聊天框上传图片提问（含纯图、图+文 两种）。
    输入：state['user_message']（可空）+ state['images']（必有至少 1 张）
    输出：final_response（VLM 回答），同时记一条假的 tool_results 便于前端展示来源。
    """
    images = state.get("images") or []
    user_message = (state.get("user_message") or "").strip()

    if not images:
        # 路由保险丝：理论上不会进来；万一进来则降级为文本提示
        return {"final_response": "未检测到图片，请直接发送文字问题。"}

    # 兜底文本：纯图无说明时给一个通用 prompt（聊天框上传图但没打字的场景）
    if not user_message:
        user_message = (
            "请仔细查看这张图片，描述图中的关键信息；"
            "如果是安全生产现场场景，请重点指出是否存在违规行为（如未戴安全帽、抽烟、玩手机、"
            "接打电话、未戴口罩等），并给出依据。"
        )

    logger.info(
        f"[VLMChat] images={len(images)} text_len={len(user_message)} "
        f"session={state.get('session_id', '')}"
    )

    # 构建多模态 message：[image..., text]
    content: list[dict] = []
    for img in images:
        url = _normalize_image_url(img)
        if not url:
            continue
        content.append({"type": "image_url", "image_url": {"url": url}})
    content.append({"type": "text", "text": user_message})

    system_prompt = (
        "你是 KSIpms 综合管理平台的智能助手，具备图像理解能力。"
        "用户在聊天框中上传了图片并发起提问，请基于图像内容给出准确、简洁的中文回答。"
        "如图像涉及安全生产现场，请关注是否存在违规行为（未戴安全帽/抽烟/玩手机/接打电话/未戴口罩等），"
        "并明确指出判断依据；不确定时请如实说明，不要编造。"
    )

    try:
        # 使用 LLM 客户端单例（VLM 调用）
        from utils.llm_pool import get_llm
        llm_config = CONFIG["llm"]
        llm = get_llm(role="vlm", temperature=0.3)
        # 为 VLM 设置额外参数（max_tokens, timeout）
        llm.max_tokens = llm_config.get("max_tokens", 2048)
        llm.request_timeout = llm_config.get("timeout", 60)
        emit_status("analyzing", "正在看图并分析…")
        answer = stream_llm(llm, [
            SystemMessage(content=system_prompt),
            HumanMessage(content=content),
        ])
    except Exception as e:
        logger.exception(f"[VLMChat] VLM 调用失败")
        answer = f"图像分析失败: {type(e).__name__}: {e}"

    # 记一条工具结果，便于前端 debug 面板展示走了 VLM 路径
    tool_result = {
        "success": True,
        "tool": "vlm_chat",
        "result": {
            "modality": "multimodal",
            "image_count": len(images),
            "answer_length": len(answer or ""),
        },
    }
    return {
        "final_response": answer,
        "tool_results": [tool_result],
        "plan": [{"task": "vlm_chat", "args": {"image_count": len(images)},
                  "status": "completed", "result": tool_result}],
        "current_task_idx": 1,
    }


# ===================== 短期记忆落盘节点（两条链路统一出口） =====================
def memorize_node(state: AgentState) -> Dict[str, Any]:
    """把本轮 [用户问题, 最终答案] 追加进 messages，由 checkpointer 按 thread_id 持久化。

    设计要点（为什么单独设一个节点）：
      - 之前 planner 自己往 messages 里塞"计划 JSON + 当前问题"，既存错了内容
        （存的是 plan 不是答案），又在有 checkpointer 后会把历史重复追加。
      - 这里作为 planner/vlm_chat 两条链路在 END 前的统一汇合点，只追加干净的
        "问题 + 答案"对（答案剥离 base64 防止图片字节进记忆），逻辑单一可靠。
      - state['messages'] 用 add_messages 累积：返回的新消息会被自动 append 到
        既有历史之后，无需手动拼接旧列表（避免重复）。

    多模态（看图）轮次同样记一条文本记忆：问题原文 + VLM 答案，
    这样下一轮纯文本提问也能指代"刚才那张图说的…"。但历史里只存文本，不存图片字节。
    """
    from graph.memory import _strip_heavy

    user_message = (state.get("user_message") or "").strip()
    final_response = state.get("final_response") or ""

    # 纯图无文字的轮次，用占位问题，保证历史可读
    if not user_message:
        if state.get("images"):
            user_message = "（上传了图片）"
        else:
            # 无问题无答案：不写记忆
            if not final_response:
                return {}

    new_msgs = [HumanMessage(content=user_message)]
    if final_response:
        new_msgs.append(AIMessage(content=_strip_heavy(final_response)))

    logger.info(f"[Memorize] 写入短期记忆：+{len(new_msgs)} 条 session={state.get('session_id','')}")
    return {"messages": new_msgs}

