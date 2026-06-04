from typing import Dict, Any
import asyncio
import json
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
  {{"task": "format_response", "args": {{"template": "今天共发生 {{count}} 类告警"}}}}
]

注意：
1. 只能使用上述列出的工具，不要编造不存在的工具
2. 参数必须符合工具定义的 schema
3. 如果用户请求无法用工具完成，返回：[{{"task": "direct_response", "args": {{"text": "..."}}}}]
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


def executor_node(state: AgentState) -> Dict[str, Any]:
    """
    执行节点：通过 Skill Registry 执行当前任务

    输入：plan, current_task_idx
    输出：tool_results, current_task_idx + 1
    """
    plan = state["plan"]
    idx = state["current_task_idx"]

    if idx >= len(plan):
        logger.info("[Executor] 所有任务已完成")
        return {"current_task_idx": idx}

    task = plan[idx]
    logger.info(f"[Executor] 执行任务 {idx + 1}/{len(plan)}: {task['task']}")

    # 通过 Skill Registry 调用
    registry = get_skill_registry()
    context = {
        "session_id": state.get("session_id", ""),
        "trace_id": state.get("trace_id", ""),
    }

    # 同步包装异步调用
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果已经在异步上下文中，创建新的 task
            import nest_asyncio
            nest_asyncio.apply()
            result_data = loop.run_until_complete(
                registry.invoke(task["task"], task["args"], context)
            )
        else:
            result_data = asyncio.run(
                registry.invoke(task["task"], task["args"], context)
            )

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

    return {
        "plan": plan,
        "current_task_idx": idx + 1,
        "tool_results": tool_results,
    }


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
            result_data = r.get("result", {})
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
