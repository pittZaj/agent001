from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class ChatRequest(BaseModel):
    """对话请求（兼容纯文本 / 文本+图片 / 纯图片 三种形式）

    与综合管理平台聊天框对接：
      - 纯文本：     {"session_id": "u1", "message": "今天有哪些告警？"}
      - 文本+图片：  {"session_id": "u1", "message": "图里有人没戴安全帽吗？", "images": ["data:image/jpeg;base64,..."]}
      - 纯图片：     {"session_id": "u1", "images": ["data:image/jpeg;base64,..."]}
    """
    session_id: str = Field(
        ...,
        description="会话 ID，同时是『短期记忆』的会话键（thread_id）：相同 session_id 的"
                    "多次请求会累积上下文、自动多轮连贯；不同 session_id 相互隔离。"
                    "务必每个独立对话用唯一且稳定的 id（推荐 UUID），切勿用常量或跨对话复用。",
    )
    message: Optional[str] = Field(
        None, description="用户文本消息（纯图片时可不传）"
    )
    images: Optional[List[str]] = Field(
        None,
        description="图片列表，每项为 base64 data URL（data:image/...;base64,xxx）或裸 base64；"
                    "也支持 http(s) 图片 URL。可多张。",
    )
    stream: bool = Field(False, description="是否流式输出（SSE）。传 true 返回 text/event-stream，不传/false 返回一次性 JSON。")


class ChatResponse(BaseModel):
    """对话响应"""
    session_id: str
    response: str
    modality: str = Field(
        "text", description="本次走的链路：text=纯文本Plan-Execute / multimodal=VLM看图对话"
    )
    plan: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []
    elapsed_ms: int


class JudgeRequest(BaseModel):
    """多模态告警复判请求（JSON 格式）"""
    image_base64: str = Field(..., description="图片 base64")
    yolo_result: Optional[Dict[str, Any]] = Field(None, description="YOLO 检测结果")
    prompt: Optional[str] = Field(
        None, description="自定义判断提示词"
    )


class JudgeResponse(BaseModel):
    """多模态告警复判响应"""
    verdict: Dict[str, int] = Field(
        ..., description="四属性判断 {smoking, helmet, phone, mask}, 0=否 1=是 2=不确定"
    )
    reasoning: str = Field(..., description="判断依据")
    confidence: float = Field(..., description="置信度")
    elapsed_ms: int


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    version: str = "0.1.0"
    llm_available: bool
