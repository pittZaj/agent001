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


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    规划节点：LLM 解析用户意图，生成任务列表

    输入：user_message
    输出：plan（任务列表）
    """
    logger.info(f"[Planner] 开始规划任务，用户消息: {state['user_message']}")

    # 从 Skill Registry 获取可用工具
    registry = get_skill_registry()
    available_skills = registry.list_skills()

    # 构造工具描述
    tools_desc = []
    for idx, skill in enumerate(available_skills, 1):
        param_desc = ", ".join([
            f"{k}: {v.get('description', v.get('type', 'any'))}"
            for k, v in skill.parameters.get("properties", {}).items()
        ])
        tools_desc.append(f"{idx}. {skill.id} - {skill.description}\n   参数: {{{param_desc}}}")

    tools_text = "\n".join(tools_desc) if tools_desc else "暂无可用工具"

    llm_config = CONFIG["llm"]
    llm = ChatOpenAI(
        base_url=llm_config["base_url"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
        temperature=0.1,  # 规划阶段用低温度
    )

    system_prompt = f"""你是一个任务规划助手。根据用户的请求，将其拆解为可执行的子任务。

可用工具：
{tools_text}

请按以下 JSON 格式返回任务列表：
[
  {{"task": "query_alarms", "args": {{"date": "2026-06-01"}}}},
  {{"task": "format_response", "args": {{"template": "今天共发生 {{{{count}}}} 类告警"}}}}
]

注意：
1. 只能使用上述列出的工具，不要编造不存在的工具
2. 参数必须符合工具定义的 schema
3. 如果用户请求无法用工具完成，返回：[{{"task": "direct_response", "args": {{"text": "..."}}}}]
4. **步骤间传参**：如果后续任务需要使用前面任务的输出，使用模板语法 {{{{step_N.field_name}}}}
   例如：步骤0返回 {{"camera_id": "CAM-005", "ts_event": 1234567890}}
        步骤1可引用：{{"task": "query_video", "args": {{"camera_id": "{{{{step_0.camera_id}}}}"}}}}
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
            # 匹配 {{step_0.camera_id}} 格式
            matches = re.findall(r'\{\{step_(\d+)\.([^}]+)\}\}', value)
            if matches:
                # 纯引用快捷路径：整个值就是一个 {{step_N.field}}，保留原始类型
                # （否则 list/dict 会被 str() 成字符串，下游工具无法使用）
                pure_match = re.fullmatch(r'\{\{step_(\d+)\.([^}]+)\}\}', value.strip())
                if pure_match:
                    step_idx, field_path = int(pure_match.group(1)), pure_match.group(2)
                    if step_idx < current_idx and step_idx in step_outputs:
                        field_value = _get_nested_field(step_outputs[step_idx], field_path)
                        if field_value is not None:
                            logger.info(f"[Executor] 解析参数(保留类型): {{{{step_{step_idx}.{field_path}}}}} -> {type(field_value).__name__}")
                            resolved[key] = field_value
                            continue
                    # 解析失败则原样保留
                    resolved[key] = value
                    continue

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


def _execute_task_mock(task: Dict[str, Any]) -> Dict[str, Any]:
    """Mock 工具执行（阶段 1 用，阶段 2 替换为真实 MCP 调用）"""
    task_name = task["task"]
    args = task["args"]

    if task_name == "query_alarms":
        return {
            "success": True,
            "tool": "query_alarms",
            "result": {
                "alarms": [
                    {"type": "no_helmet", "count": 5},
                    {"type": "smoking", "count": 2},
                    {"type": "phone", "count": 1},
                ]
            }
        }
    elif task_name == "query_video":
        return {
            "success": True,
            "tool": "query_video",
            "result": {"video_url": f"http://video.internal/clip/{args.get('camera_id')}_mock.mp4"}
        }
    elif task_name == "direct_response":
        return {
            "success": True,
            "tool": "direct_response",
            "result": {"text": args.get("text", "")}
        }
    else:
        return {
            "success": False,
            "tool": task_name,
            "error": f"未知任务类型: {task_name}"
        }


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

    system_prompt = """你是一个友好的助手。根据用户的问题和工具调用结果，用自然语言生成简洁清晰的回答。

要求：
1. 直接回答用户的问题，不要重复问题
2. 用中文回答，结构清晰
3. 如果有数据，用列表或表格的形式呈现
4. 不要编造数据，只基于工具返回的真实结果
5. 如果工具失败，说明失败原因
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
