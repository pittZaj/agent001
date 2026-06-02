"""
自动生成的 LangGraph Agent: alarm_query_agent
"""
import json
from typing import TypedDict, List, Dict, Any
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END


# ==================== 状态定义 ====================
class AgentState(TypedDict):
    """Agent 状态"""
    user_message: str
    plan: List[Dict[str, Any]]
    current_task_idx: int
    tool_results: List[Dict[str, Any]]
    final_response: str
    error: str | None


# ==================== System Prompt ====================
SYSTEM_PROMPT = """
你是一个专业的任务执行智能体：alarm_query_agent

# 任务职责
查询告警记录

# 可用工具
1. **query_alarms**: 查询告警记录
   参数：
   - date: 日期 YYYY-MM-DD

# 工作流程
1. 仔细分析用户的请求，理解其意图
2. 将复杂任务拆解为可执行的步骤
3. 按顺序调用合适的工具完成每个步骤
4. 汇总结果，生成结构化的回复

# 规则
- 只使用上述列出的工具，不要臆造不存在的工具
- 工具参数必须从用户请求中提取或合理推断
- 如果信息不足，主动询问用户
- 返回的结果要清晰、准确、有条理

# 输出格式
始终以 JSON 格式返回执行计划：
{
  "plan": [
    {"task": "tool_name", "args": {"param": "value"}, "reason": "为什么需要这一步"},
    ...
  ]
}

"""


# ==================== 工具模拟（MVP 版本）====================
def mock_tool_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """模拟工具调用（MVP 阶段）

    Args:
        tool_name: 工具名称
        args: 工具参数

    Returns:
        模拟的工具返回结果
    """
    # MVP: 返回模拟数据
    if tool_name == "query_alarms":
        return {
            "total": 8,
            "alarms": [
                {"type": "no_helmet", "count": 5, "camera": "A01"},
                {"type": "smoking", "count": 2, "camera": "B03"},
                {"type": "phone", "count": 1, "camera": "A02"}
            ]
        }
    elif tool_name == "query_video":
        return {
            "video_url": "http://example.com/video/123.mp4",
            "duration": 120
        }
    elif tool_name == "query_person":
        return {
            "person_id": "P001",
            "name": "张三",
            "department": "施工部"
        }
    else:
        return {"error": f"未知工具: {tool_name}"}


# ==================== 图节点 ====================
def planner(state: AgentState) -> AgentState:
    """规划节点：解析用户意图，生成执行计划"""
    llm = ChatOpenAI(
        base_url="http://127.0.0.1:8004/v1",
        api_key="EMPTY",
        model="Qwen3-VL-4B-Instruct-FP8",
        temperature=0.2
    )

    user_message = state["user_message"]

    # 调用 LLM 生成计划
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    try:
        response = llm.invoke(messages)
        content = response.content

        # 尝试解析 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]

        plan_data = json.loads(content)
        plan = plan_data.get("plan", [])

    except Exception as e:
        # 解析失败，返回错误
        state["error"] = f"规划失败: {str(e)}"
        return state

    state["plan"] = plan
    state["current_task_idx"] = 0
    return state


def executor(state: AgentState) -> AgentState:
    """执行节点：逐个执行计划中的任务"""
    plan = state["plan"]
    current_idx = state["current_task_idx"]

    if current_idx >= len(plan):
        # 所有任务已完成
        return state

    # 执行当前任务
    task = plan[current_idx]
    tool_name = task.get("task")
    args = task.get("args", {})

    # 调用工具（MVP: mock）
    result = mock_tool_call(tool_name, args)

    # 记录结果
    state["tool_results"].append({
        "task": tool_name,
        "args": args,
        "result": result
    })

    # 更新索引
    state["current_task_idx"] += 1

    return state


def should_continue(state: AgentState) -> str:
    """判断是否继续执行"""
    if state.get("error"):
        return "format_response"

    if state["current_task_idx"] >= len(state["plan"]):
        return "format_response"

    return "execute"


def format_response(state: AgentState) -> AgentState:
    """格式化最终响应"""
    if state.get("error"):
        state["final_response"] = f"执行失败: {state['error']}"
        return state

    # 汇总工具结果
    tool_results = state["tool_results"]

    # 简单格式化（MVP 版本）
    lines = ["执行结果：", ""]
    for i, result in enumerate(tool_results, 1):
        lines.append(f"{i}. 工具: {result['task']}")
        lines.append(f"   参数: {result['args']}")
        lines.append(f"   结果: {json.dumps(result['result'], ensure_ascii=False, indent=2)}")
        lines.append("")

    state["final_response"] = "\n".join(lines)
    return state


# ==================== 构建图 ====================
def build_graph() -> StateGraph:
    """构建 LangGraph 图"""
    workflow = StateGraph(AgentState)

    # 添加节点（避免与状态键冲突）
    workflow.add_node("planner", planner)
    workflow.add_node("executor", executor)
    workflow.add_node("formatter", format_response)

    # 添加边
    workflow.set_entry_point("planner")
    workflow.add_conditional_edges(
        "planner",
        lambda s: "executor" if not s.get("error") else "formatter"
    )
    workflow.add_conditional_edges(
        "executor",
        should_continue,
        {
            "execute": "executor",
            "format_response": "formatter"
        }
    )
    workflow.add_edge("formatter", END)

    return workflow.compile()


# ==================== 运行接口 ====================
def run(user_message: str) -> Dict[str, Any]:
    """运行 Agent

    Args:
        user_message: 用户输入

    Returns:
        执行结果
    """
    graph = build_graph()

    initial_state = {
        "user_message": user_message,
        "plan": [],
        "current_task_idx": 0,
        "tool_results": [],
        "final_response": "",
        "error": None
    }

    final_state = graph.invoke(initial_state)

    return {
        "response": final_state.get("final_response", ""),
        "plan": final_state.get("plan", []),
        "tool_results": final_state.get("tool_results", []),
        "error": final_state.get("error")
    }


if __name__ == "__main__":
    # 测试
    result = run("今天发生了哪几种告警？")
    print(json.dumps(result, ensure_ascii=False, indent=2))
