from langgraph.graph import StateGraph, START, END
from loguru import logger

from graph.state import AgentState
from graph.nodes import (
    planner_node, executor_node, formatter_node, should_continue,
    vlm_chat_node, route_by_modality, memorize_node,
)
from graph.memory import get_checkpointer


def build_graph():
    """
    构建 KSAgent 主流程图（Plan-Execute 模式 + 多模态分支 + 短期记忆）

    流程：
        START → [route_by_modality]
                  ├─ images 非空 → vlm_chat → memorize → END   （ChatGPT 式看图对话）
                  └─ 仅文本     → planner → executor → ... → formatter → memorize → END

    记忆（短期/会话级）：
        - 编译时挂载 checkpointer（进程内 MemorySaver），按 config.thread_id 持久化 state；
        - memorize 节点作为两条链路在 END 前的统一出口，把本轮 [问题, 答案] 写入 messages；
        - 下一轮相同 thread_id 进入时，state['messages'] 已含历史，planner/formatter
          会将其作为上下文注入 LLM，实现多轮连贯。
    """
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("formatter", formatter_node)
    graph.add_node("vlm_chat", vlm_chat_node)
    graph.add_node("memorize", memorize_node)

    # 入口：根据是否带图分流
    graph.add_conditional_edges(
        START,
        route_by_modality,
        {
            "vlm_chat": "vlm_chat",   # 带图 → VLM 直接对话
            "planner": "planner",      # 仅文本 → 原 Plan-Execute 链路
        },
    )

    # 文本链路：planner → executor → ... → formatter → memorize
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges(
        "executor",
        should_continue,
        {
            "execute": "executor",  # 还有任务，继续执行
            "format": "formatter",  # 所有任务完成，进入格式化
        },
    )
    graph.add_edge("formatter", "memorize")

    # 多模态链路：vlm_chat → memorize
    graph.add_edge("vlm_chat", "memorize")

    # 统一出口：写入短期记忆后结束
    graph.add_edge("memorize", END)

    # 挂载 checkpointer：按 thread_id 自动保存/加载会话状态（短期记忆）
    compiled = graph.compile(checkpointer=get_checkpointer())
    logger.info("LangGraph 图构建完成（含多模态分支 + 短期记忆 checkpointer）")
    return compiled


# 全局单例
_graph = None

def get_graph():
    """获取已编译的图（单例）"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
