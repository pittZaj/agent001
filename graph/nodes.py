from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from loguru import logger

from graph.state import AgentState
from utils import CONFIG


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    规划节点：LLM 解析用户意图，生成任务列表

    输入：user_message
    输出：plan（任务列表）
    """
    logger.info(f"[Planner] 开始规划任务，用户消息: {state['user_message']}")

    llm_config = CONFIG["llm"]
    llm = ChatOpenAI(
        base_url=llm_config["base_url"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
        temperature=0.1,  # 规划阶段用低温度
    )

    system_prompt = """你是一个任务规划助手。根据用户的请求，将其拆解为可执行的子任务。

可用工具：
1. query_alarms - 查询告警记录
2. query_video - 检索录像片段
3. query_person - 查询人员信息
4. vlm_judge - 多模态图像判断（抽烟/安全帽/手机/口罩）
5. rag_query - 查询规章制度知识库

请按以下 JSON 格式返回任务列表：
[
  {"task": "query_alarms", "args": {"date": "2026-06-01"}},
  {"task": "format_response", "args": {"template": "今天共发生 {count} 类告警"}}
]

如果用户请求无法用工具完成，返回：
[{"task": "direct_response", "args": {"text": "..."}}]
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
    执行节点：执行当前任务

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

    # Mock 工具调用（阶段 1）
    result = _execute_task_mock(task)

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

    输入：tool_results
    输出：final_response
    """
    logger.info("[Formatter] 格式化最终响应")

    tool_results = state.get("tool_results", [])

    # 简单实现：拼接所有结果
    response_parts = []
    for r in tool_results:
        if r.get("success"):
            if r["tool"] == "query_alarms":
                alarms = r["result"]["alarms"]
                response_parts.append(f"今天共发生 {len(alarms)} 类告警：")
                for a in alarms:
                    response_parts.append(f"- {a['type']}: {a['count']} 次")
            elif r["tool"] == "query_video":
                response_parts.append(f"录像地址：{r['result']['video_url']}")
            elif r["tool"] == "direct_response":
                response_parts.append(r["result"]["text"])
        else:
            response_parts.append(f"❌ {r['tool']} 执行失败: {r.get('error', '未知错误')}")

    final_response = "\n".join(response_parts) if response_parts else "无结果"

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
