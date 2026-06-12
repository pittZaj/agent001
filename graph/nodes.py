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


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    规划节点：LLM 解析用户意图，生成任务列表

    输入：user_message
    输出：plan（任务列表）
    """
    logger.info(f"[Planner] 开始规划任务，用户消息: {state['user_message']}")

    # 从 Skill Registry 获取可用工具，按平台分类组织
    registry = get_skill_registry()
    available_skills = registry.list_skills()
    tools_text = _format_skills_grouped(available_skills) or "暂无可用工具"

    llm_config = CONFIG["llm"]
    llm = ChatOpenAI(
        base_url=llm_config["base_url"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
        temperature=0.1,  # 规划阶段用低温度
    )

    # 注入当前真实日期时间（关键：LLM 训练数据日期滞后，必须显式告知，否则相对时间会算错年份）
    from datetime import datetime, timedelta
    _now = datetime.now()
    _today = _now.strftime("%Y-%m-%d")
    _now_str = _now.strftime("%Y-%m-%d %H:%M:%S")
    _yesterday = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
    _7days_ago = (_now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    _30days_ago = (_now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    _2hours_ago = (_now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    system_prompt = f"""你是 KSIpms 综合管理平台的智能任务规划助手。请根据用户请求，将其拆解为可执行的子任务序列。

# ⏰ 当前真实时间（务必以此为准计算时间范围，不要使用你训练数据中的日期！）
- **现在**：{_now_str}
- **今天**：{_today}
- **昨天**：{_yesterday}
- 最近 2 小时起点：{_2hours_ago}
- 最近 7 天/一周起点：{_7days_ago}
- 最近 30 天/一个月起点：{_30days_ago}

**时间筛选规则（重要）**：
- "今天" → time_start="{_today} 00:00:00", time_end="{_today} 23:59:59"
- "昨天" → time_start="{_yesterday} 00:00:00", time_end="{_yesterday} 23:59:59"
- "最近N小时/天/周/月" → time_start=对应起点, time_end="{_now_str}"
- "所有/全部"（无时间限定）→ **不要传 time_start/time_end**，让平台返回全量数据
- 年份必须是 {_now.year} 年，绝不能用 2024/2025 等过去年份

# 可用工具（已对接真实平台 192.168.1.199:6620 MCP Server）
{tools_text}

# 工具选择指引（重要）
- 查 AI 视觉算法告警（越界/离岗/未戴安全帽/吸烟等）→ 用 `ai_event_*`，**不是** `system_alarm_*`
- 查服务器/磁盘/服务基础设施告警 → 用 `system_alarm_*`
- AI 告警的复判/回写：先 `vlm_judge_alarm`（输入 alarm_uuid），再 `update_alarm_status`（verdict 自动映射 review_status）
- 统计/趋势分析 → 用 `aggregate_alarms`（消费 ai_event_list）+ `visualize_alarms`（生成图表）
- 查规章制度/处罚标准 → 用 `kb_regulation`
- 查录像片段：用 `fetch_alarm_context`（输入 alarm_uuid，自动解析摄像头与时间窗）
- 查 AI 摄像机/视频设备列表 → 用 `video_device_list`，**不要**用 system_role_camera_permission（那是按角色查权限）
- 仅闲聊或无法用工具完成 → 用 `direct_response`

# 真实平台关键字段（与旧版有差异，务必对齐）
- AI 事件主键：`uuid`（不是 alarm_uuid）→ ai_event_* 工具入参用 `event_uuid`
- 时间字段：`created_at`，格式 "yyyy-MM-dd HH:mm:ss"；筛选用 `time_start`/`time_end`
- 告警类型：`event_type`（如 ET03007）/ `event_name`（中文，如"未戴安全帽告警"）
- 摄像机：`camera_uuid` / `camera_name`
- 复核状态 review_status：1=待复核 2=已复核 3=已完成 5=误报

# 步骤间传参
- 引用前序步骤输出：`{{{{step_N.field_name}}}}`，支持嵌套如 `{{{{step_0.events.0.uuid}}}}`
- 纯引用（整个值就是一个 {{{{...}}}}）会保留原始类型（list/dict 不会被字符串化）

# 输出格式
仅返回 JSON 数组，不要包裹任何其他文字：
[
  {{"task": "ai_event_list", "args": {{"pageno": 1, "pagesize": 5}}}},
  {{"task": "vlm_judge_alarm", "args": {{"alarm_uuid": "{{{{step_0.events.0.uuid}}}}"}}}},
  {{"task": "update_alarm_status", "args": {{"alarm_uuid": "{{{{step_0.events.0.uuid}}}}", "verdict": "{{{{step_1.verdict}}}}", "note": "VLM 自动复判"}}}}
]

# 常见任务模板（重要：按时间范围查询不要限制 pagesize，让系统自动拉全量）
- "统计每种告警类型数量并画柱状图"：
  [{{"task":"aggregate_alarms","args":{{"group_by":"event_name"}}}},
   {{"task":"visualize_alarms","args":{{"data":"{{{{step_0}}}}","chart_type":"bar","title":"告警类型分布"}}}}]
- "复判告警 <UUID> 并回写状态"：
  [{{"task":"vlm_judge_alarm","args":{{"alarm_uuid":"<UUID>"}}}},
   {{"task":"update_alarm_status","args":{{"alarm_uuid":"<UUID>","verdict":"{{{{step_0.verdict}}}}"}}}}]
- "查最近/前 N 条 AI 告警"（强调数量）：[{{"task":"ai_event_list","args":{{"pageno":1,"pagesize":N}}}}]
- "查今天/昨天/前天/某日期的 AI 告警"（按时间）：[{{"task":"ai_event_list","args":{{"time_start":"...", "time_end":"..."}}}}]（不传 pagesize，系统会自动分页拉全量并生成统计摘要）

# 约束
1. 只能使用上述列出的工具，不要编造
2. 参数名必须严格匹配工具 schema（如 ai_event_* 用 event_uuid，不是 alarm_uuid）
3. 无法完成时返回 `[{{"task":"direct_response","args":{{"text":"..."}}}}]`
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["user_message"]),
    ]

    try:
        response = llm.invoke(messages)
        content = response.content

        # 解析 JSON
        import json
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
            logger.info(f"[Planner] 生成计划: {plan}")
            return {
                "plan": [{"task": t["task"], "args": t["args"], "status": "pending"} for t in plan],
                "current_task_idx": 0,
                "messages": state.get("messages", []) + [HumanMessage(content=state["user_message"]), response],
            }
        else:
            # 降级：直接回复
            logger.warning(f"[Planner] 无法解析计划，降级为直接回复")
            return {
                "plan": [{"task": "direct_response", "args": {"text": content}, "status": "pending"}],
                "current_task_idx": 0,
                "messages": state.get("messages", []) + [HumanMessage(content=state["user_message"]), response],
            }

    except Exception as e:
        logger.error(f"[Planner] 规划失败: {e}")
        return {
            "plan": [{"task": "direct_response", "args": {"text": f"规划失败: {e}"}, "status": "failed"}],
            "current_task_idx": 0,
            "error": str(e),
        }


async def _fetch_all_events_async(registry, args: dict, context: dict, first_result: dict) -> dict:
    """
    ai_event_list 自动分页拉取全量数据（在单个事件循环内完成所有分页）

    关键：所有分页调用都在同一个 await 链里，复用同一个 MCP 连接的 event loop，
    避免跨 event loop 导致的 ClosedResourceError（之前同步循环每页 asyncio.run 会失败）。

    限制：
    - 最多拉取 max_pages 页（防止无限循环）
    - 单页失败则停止，保留已拉取数据
    """
    total = first_result.get('total', 0)
    pagesize = args.get('pagesize', 20)
    current_events = first_result.get('events', [])

    # 如果第一页已是全部数据，直接返回
    if total <= len(current_events):
        return first_result

    # 为了减少请求数，分页时用大 pagesize（最大 10000）
    fetch_pagesize = 10000
    logger.info(
        f"[Executor] ai_event_list 检测到大数据量 (total={total}, 首页 {len(current_events)} 条)，"
        f"开始分页拉取全量（每页 {fetch_pagesize} 条）..."
    )

    all_events = list(current_events)
    # 用大 pagesize 重新从第 1 页拉，避免首页小 pagesize 与后续对不齐
    all_events = []
    pageno = 1
    max_pages = 100  # 100 * 10000 = 100万条上限

    while len(all_events) < total and pageno <= max_pages:
        page_args = dict(args)
        page_args['pageno'] = pageno
        page_args['pagesize'] = fetch_pagesize

        try:
            page_result = await registry.invoke("ai_event_list", page_args, context)
        except Exception as e:
            logger.warning(f"[Executor] 第 {pageno} 页拉取异常: {e}，停止分页")
            break

        if page_result.get('error'):
            logger.warning(f"[Executor] 第 {pageno} 页拉取失败: {page_result['error']}，停止分页")
            break

        page_events = page_result.get('events', [])
        if not page_events:
            break

        all_events.extend(page_events)
        logger.info(f"[Executor] 已拉取 {len(all_events)}/{total} 条数据（第 {pageno} 页）")

        if len(all_events) >= total or len(page_events) < fetch_pagesize:
            break
        pageno += 1

    final_result = dict(first_result)
    final_result['events'] = all_events
    final_result['_fetched_pages'] = pageno
    final_result['_fetched_count'] = len(all_events)

    logger.info(f"[Executor] ai_event_list 分页拉取完成，共 {len(all_events)}/{total} 条数据")
    return final_result


def _run_async(coro):
    """在 同步/异步 两种上下文中安全运行协程。

    - 无运行中事件循环（如 graph.invoke 同步调用）：用独立线程跑 asyncio.run，
      避免 Python 3.12 下 get_event_loop 抛 RuntimeError。
    - 有运行中循环：用 nest_asyncio 复用当前循环。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        # 在新线程里跑一个干净的事件循环，避免污染调用方
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    else:
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(coro)


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

    # 步骤间传参：替换参数中的模板变量
    args = task["args"].copy() if task["args"] else {}
    step_outputs = state.get("step_outputs", {})

    args = _resolve_step_references(args, step_outputs, idx)

    # 通过 Skill Registry 调用
    registry = get_skill_registry()
    context = {
        "session_id": state.get("session_id", ""),
        "trace_id": state.get("trace_id", ""),
    }

    # 同步包装异步调用（兼容 有/无 运行中事件循环 两种情况）
    try:
        if task["task"] == "ai_event_list":
            # ai_event_list：首次调用 + 自动分页全部在同一个事件循环内完成
            # （避免跨 event loop 的 ClosedResourceError，确保统计基于全量数据）
            async def _invoke_with_paging():
                first = await registry.invoke("ai_event_list", args, context)
                if first.get("error"):
                    return first
                return await _fetch_all_events_async(registry, args, context, first)

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

        # 等级
        stats['levels'][e.get('level', 'unknown')] += 1

        # 复核状态
        rs = e.get('review_status', 'unknown')
        rs_map = {1: '待复核', 2: '已复核', 3: '已完成', 5: '误报', '1': '待复核', '2': '已复核', '3': '已完成', '5': '误报'}
        stats['review_status'][rs_map.get(rs, str(rs))] += 1

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
            level_color_map = {'red': '#dc3545', 'orange': '#fd7e14', 'yellow': '#ffc107', 'blue': '#0d6efd', 'unknown': '#6c757d'}
            labels = list(level_data.keys())
            sizes = list(level_data.values())
            colors_l = [level_color_map.get(l, '#6c757d') for l in labels]
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
                level_emoji = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'blue': '🔵'}
                for level, count in stats['levels'].most_common():
                    pct = count / stats['total'] * 100
                    emoji = level_emoji.get(level, '⚪')
                    report.append(f"- {emoji} **{level}**: {count} 条 ({pct:.1f}%)")
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
                report.append(f"**统计维度**：按{group_label}分组")
                if sampled:
                    report.append(f"**数据规模**：平台共 {platform_total} 条，本次统计 {total} 条（采样）\n")
                else:
                    report.append(f"**告警总数**：{total} 条（平台完整数据）\n")

                report.append(f"### 分布明细")
                for item in agg_data[:15]:
                    key = item.get('key', 'unknown')
                    count = item.get('count', 0)
                    pct = count / total * 100 if total > 0 else 0
                    # 简易文本条形（直观展示占比）
                    bar = '█' * max(1, int(pct / 5))
                    report.append(f"- **{key}**：{count} 条 ({pct:.1f}%) {bar}")

                # 为聚合结果生成柱状图
                chart_b64 = _generate_aggregate_chart(agg_data, group_label, total)
                if chart_b64:
                    chart_image_base64 = chart_b64
                    report.append(f"\n### 📊 可视化图表")
                    report.append(f"已生成「{group_label}分布」柱状图\n")

                response_parts.append("\n".join(report))
            else:
                response_parts.append("❌ 未统计到符合条件的告警数据")

        elif tool_name == "visualize_alarms":
            if 'image_base64' in result:
                response_parts.append("✅ 已生成可视化图表")
                if not chart_image_base64:
                    chart_image_base64 = result['image_base64']

        else:
            # 其他工具，简单提示
            response_parts.append(f"✅ {tool_name} 执行完成")

    final_response = "\n\n---\n\n".join(response_parts)

    # 将图表直接内联到 markdown（Gradio Chatbot 支持 base64 图片渲染），
    # 不依赖 Web 层单独取 chart_image_base64 字段，确保"已生成图表"能真正显示。
    if chart_image_base64:
        final_response += f"\n\n![统计图表](data:image/png;base64,{chart_image_base64})"

    return {
        "final_response": final_response,
        "chart_image_base64": chart_image_base64,  # 兼容其他调用方
    }


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

    # 如果只有 direct_response，直接返回
    if (len(tool_results) == 1
            and tool_results[0].get("success")
            and tool_results[0].get("tool") == "direct_response"):
        return {"final_response": tool_results[0]["result"].get("text", "")}

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
    should_use_summary = has_large_data or has_event_array or has_aggregate or len(tools_summary) > 4000
    if should_use_summary:
        logger.info(
            f"[Formatter] 触发结构化摘要路径 "
            f"(large_data={has_large_data}, event_array={has_event_array}, "
            f"aggregate={has_aggregate}, summary_len={len(tools_summary)})"
        )
        return _generate_summary_response(user_message, tool_results)

    # 用 LLM 生成自然语言回答
    llm_config = CONFIG["llm"]
    llm = ChatOpenAI(
        base_url=llm_config["base_url"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
        temperature=0.3,
    )

    system_prompt = """你是 KSIpms 综合管理平台的智能助手。根据用户问题和工具调用结果，用自然语言生成简洁清晰的回答。

# 真实平台关键字段速查（解读工具结果时使用）
- AI 事件: `events[]`，每项含 uuid / event_type / event_name / camera_name / created_at / img_path / level / review_status
  · review_status: 1=待复核 2=已复核 3=已完成 5=误报
  · level: red/orange/yellow/blue
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
"""

    user_prompt = f"""用户问题：{user_message}

工具调用结果：
{tools_summary}

请根据以上信息生成回答。"""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        final_response = response.content
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
