from langgraph.graph import StateGraph, START, END
from loguru import logger

from graph.state import AgentState
from graph.nodes import planner_node, executor_node, formatter_node, should_continue


def build_graph():
    """
    构建 KSAgent 主流程图（Plan-Execute 模式）

    流程：
        START → Planner → Executor → [循环执行] → Formatter → END
    """
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("formatter", formatter_node)

    # 添加边
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")

    # 条件边：根据是否还有任务，决定继续执行还是进入格式化
    graph.add_conditional_edges(
        "executor",
        should_continue,
        {
            "execute": "executor",  # 还有任务，继续执行
            "format": "formatter",  # 所有任务完成，进入格式化
        },
    )

    graph.add_edge("formatter", END)

    compiled = graph.compile()
    logger.info("LangGraph 图构建完成")
    return compiled


# 全局单例
_graph = None

def get_graph():
    """获取已编译的图（单例）"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
