from typing import TypedDict, List, Dict, Any, Annotated
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """LangGraph 状态定义"""

    # 会话信息
    session_id: str
    user_message: str

    # Plan-Execute 流程
    plan: List[Dict[str, Any]]  # [{"task": "query_alarms", "args": {...}, "status": "pending"}]
    current_task_idx: int

    # 工具调用结果
    tool_results: List[Dict[str, Any]]

    # 最终响应
    final_response: str

    # 错误信息
    error: str | None

    # 消息历史（LangChain 标准格式）
    messages: Annotated[list, add_messages]


class VLMJudgeInput(TypedDict):
    """VLM 复判输入"""
    image_path: str | None
    image_base64: str | None
    yolo_result: Dict[str, Any] | None
    prompt: str


class VLMJudgeOutput(TypedDict):
    """VLM 复判输出"""
    verdict: Dict[str, int]  # {"smoking": 0, "helmet": 1, "phone": 0, "mask": 2}
    reasoning: str
    confidence: float
