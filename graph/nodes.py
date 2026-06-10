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

    system_prompt = f"""你是 KSIpms 综合管理平台的智能任务规划助手。请根据用户请求，将其拆解为可执行的子任务序列。

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

# 常见任务模板
- "统计每种告警类型数量并画柱状图"：
  [{{"task":"aggregate_alarms","args":{{"group_by":"event_name"}}}},
   {{"task":"visualize_alarms","args":{{"data":"{{{{step_0}}}}","chart_type":"bar","title":"告警类型分布"}}}}]
- "复判告警 <UUID> 并回写状态"：
  [{{"task":"vlm_judge_alarm","args":{{"alarm_uuid":"<UUID>"}}}},
   {{"task":"update_alarm_status","args":{{"alarm_uuid":"<UUID>","verdict":"{{{{step_0.verdict}}}}"}}}}]
- "查最近 N 条 AI 告警"：[{{"task":"ai_event_list","args":{{"pageno":1,"pagesize":N}}}}]

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


def _strip_large_fields(data, _max_str=800):
    """递归剥离工具结果里的超大字段（如 base64 图片），避免撑爆 LLM 上下文。

    base64 图片等只保留占位摘要，formatter 只需知道"有一张图"即可。
    """
    BIG_KEYS = {"image_base64", "image", "snapshot_base64", "thumbnail"}
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in BIG_KEYS and isinstance(v, str):
                out[k] = f"<已生成图片, {len(v)} 字节, 省略内容>"
            elif isinstance(v, str) and len(v) > _max_str:
                out[k] = v[:_max_str] + f"...<截断, 共{len(v)}字符>"
            else:
                out[k] = _strip_large_fields(v, _max_str)
        return out
    if isinstance(data, list):
        return [_strip_large_fields(x, _max_str) for x in data]
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
    for r in tool_results:
        if r.get("success"):
            tool_name = r["tool"]
            result_data = _strip_large_fields(r.get("result", {}))
            summary_parts.append(f"[工具 {tool_name} 返回]\n{json.dumps(result_data, ensure_ascii=False, indent=2)}")
        else:
            summary_parts.append(f"[工具 {r['tool']} 执行失败: {r.get('error', '未知错误')}]")

    tools_summary = "\n\n".join(summary_parts)

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
