"""
代码生成器

基于 system prompt 和工具定义，生成可执行的 LangGraph Agent 代码
"""
from typing import Dict, List, Any


class CodeGenerator:
    """LangGraph Agent 代码生成器"""

    def __init__(self):
        pass

    def generate(self, prompt: str, tools: List[Any], agent_name: str = "generated_agent") -> str:
        """生成 LangGraph Agent 代码

        Args:
            prompt: System prompt
            tools: 工具列表
            agent_name: Agent 名称

        Returns:
            生成的 Python 代码
        """
        # 提取工具名称
        tool_names = self._extract_tool_names(tools)

        # 生成代码
        code = f'''"""
自动生成的 LangGraph Agent: {agent_name}
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
{prompt}
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
        return {{
            "total": 8,
            "alarms": [
                {{"type": "no_helmet", "count": 5, "camera": "A01"}},
                {{"type": "smoking", "count": 2, "camera": "B03"}},
                {{"type": "phone", "count": 1, "camera": "A02"}}
            ]
        }}
    elif tool_name == "query_video":
        return {{
            "video_url": "http://example.com/video/123.mp4",
            "duration": 120
        }}
    elif tool_name == "query_person":
        return {{
            "person_id": "P001",
            "name": "张三",
            "department": "施工部"
        }}
    else:
        return {{"error": f"未知工具: {{tool_name}}"}}


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
        {{"role": "system", "content": SYSTEM_PROMPT}},
        {{"role": "user", "content": user_message}}
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
        state["error"] = f"规划失败: {{str(e)}}"
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
    args = task.get("args", {{}})

    # 调用工具（MVP: mock）
    result = mock_tool_call(tool_name, args)

    # 记录结果
    state["tool_results"].append({{
        "task": tool_name,
        "args": args,
        "result": result
    }})

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
        state["final_response"] = f"执行失败: {{state['error']}}"
        return state

    # 汇总工具结果
    tool_results = state["tool_results"]

    # 简单格式化（MVP 版本）
    lines = ["执行结果：", ""]
    for i, result in enumerate(tool_results, 1):
        lines.append(f"{{i}}. 工具: {{result['task']}}")
        lines.append(f"   参数: {{result['args']}}")
        lines.append(f"   结果: {{json.dumps(result['result'], ensure_ascii=False, indent=2)}}")
        lines.append("")

    state["final_response"] = "\\n".join(lines)
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
        {{
            "execute": "executor",
            "format_response": "formatter"
        }}
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

    initial_state = {{
        "user_message": user_message,
        "plan": [],
        "current_task_idx": 0,
        "tool_results": [],
        "final_response": "",
        "error": None
    }}

    final_state = graph.invoke(initial_state)

    return {{
        "response": final_state.get("final_response", ""),
        "plan": final_state.get("plan", []),
        "tool_results": final_state.get("tool_results", []),
        "error": final_state.get("error")
    }}


if __name__ == "__main__":
    # 测试
    result = run("今天发生了哪几种告警？")
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''

        return code

    def _extract_tool_names(self, tools: List[Any]) -> List[str]:
        """提取工具名称列表

        Args:
            tools: 工具定义列表

        Returns:
            工具名称列表
        """
        names = []
        for tool in tools:
            if isinstance(tool, str):
                names.append(tool)
            elif isinstance(tool, dict):
                names.append(tool.get("name", "unknown"))
        return names


if __name__ == "__main__":
    # 测试代码生成
    generator = CodeGenerator()

    prompt = """你是一个告警查询智能体。
用户可以询问今天的告警记录，你需要调用 query_alarms 工具获取数据。"""

    tools = [
        {
            "name": "query_alarms",
            "description": "查询告警记录",
            "parameters": {
                "date": "日期 YYYY-MM-DD"
            }
        }
    ]

    code = generator.generate(prompt, tools, "alarm_query_agent")
    print(code)
