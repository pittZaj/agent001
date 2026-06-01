from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class ChatRequest(BaseModel):
    """文本对话请求"""
    session_id: str = Field(..., description="会话 ID")
    message: str = Field(..., description="用户消息")
    stream: bool = Field(False, description="是否流式输出")


class ChatResponse(BaseModel):
    """文本对话响应"""
    session_id: str
    response: str
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
