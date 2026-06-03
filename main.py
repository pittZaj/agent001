"""
KSAgent FastAPI 入口

启动方式：
    cd /mnt/data3/clip/LangGraph/agent
    python main.py
    # 或
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import sys
import time
from pathlib import Path

# 添加当前目录到 sys.path（便于直接 python main.py 启动）
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from utils import CONFIG
from models import (
    ChatRequest,
    ChatResponse,
    JudgeRequest,
    JudgeResponse,
    HealthResponse,
)
from graph import get_graph
from utils.vlm import get_vlm_client


app = FastAPI(
    title="KSAgent",
    description="安全生产场景 AI 智能体 - 基于 LangGraph + Qwen3-VL",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===================== Agent-of-Agent 注册路由 =====================
class GeneratedAgentChatRequest(BaseModel):
    message: str
    trace_id: str | None = None


def _mount_generated_agents(app: FastAPI) -> None:
    """启动时把 agent/agent/registry/agent_registry.json 中的 Agent 挂到 /agents/<name>/chat。"""
    aoa_root = HERE / "agent"
    if str(aoa_root) not in sys.path:
        sys.path.insert(0, str(aoa_root))
    try:
        from registry import list_agents, load_agent_run  # type: ignore
    except ImportError as e:
        logger.warning(f"Agent-of-Agent 注册表未就绪: {e}")
        return

    for name, info in list_agents().items():
        try:
            run_fn = load_agent_run(name)
        except Exception as e:
            logger.warning(f"加载已发布 agent 失败 name={name}: {e}")
            continue

        route = info.get("route", f"/agents/{name}/chat")

        async def _handler(req: GeneratedAgentChatRequest, _run=run_fn, _name=name):
            try:
                out = _run(req.message, trace_id=req.trace_id or "")
            except Exception as e:
                logger.exception(f"[generated-agent:{_name}] 失败")
                raise HTTPException(status_code=500, detail=str(e))
            return out

        app.post(route, name=f"generated_{name}")(_handler)
        logger.info(f"挂载已发布 Agent: {name} -> {route}")


# ===================== 启动事件 =====================
@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("🚀 KSAgent 启动中...")
    logger.info(f"   LLM 后端: {CONFIG['llm']['base_url']}")
    logger.info(f"   模型名称: {CONFIG['llm']['model']}")
    logger.info("=" * 60)

    # 预热：构建图
    get_graph()
    logger.info("LangGraph 图已就绪")

    # 挂载所有已发布的 Agent-of-Agent 产出
    _mount_generated_agents(app)


# ===================== API 端点 =====================
@app.get("/health", response_model=HealthResponse)
async def health():
    """健康检查"""
    llm_available = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{CONFIG['llm']['base_url']}/models")
            llm_available = r.status_code == 200
    except Exception as e:
        logger.warning(f"LLM 健康检查失败: {e}")

    return HealthResponse(
        status="ok" if llm_available else "degraded",
        version="0.1.0",
        llm_available=llm_available,
    )


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """文本对话（自然语言操作）

    示例：
        POST /api/v1/chat
        {"session_id": "user123", "message": "今天发生了哪几种告警事件？"}
    """
    t0 = time.time()
    logger.info(f"[Chat] session={request.session_id} msg={request.message}")

    try:
        graph = get_graph()
        initial_state = {
            "session_id": request.session_id,
            "user_message": request.message,
            "plan": [],
            "current_task_idx": 0,
            "tool_results": [],
            "final_response": "",
            "error": None,
            "messages": [],
        }

        # 同步调用图
        final_state = graph.invoke(initial_state)

        elapsed_ms = int((time.time() - t0) * 1000)
        return ChatResponse(
            session_id=request.session_id,
            response=final_state.get("final_response", ""),
            plan=final_state.get("plan", []),
            tool_calls=final_state.get("tool_results", []),
            elapsed_ms=elapsed_ms,
        )

    except Exception as e:
        logger.exception(f"[Chat] 处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/judge", response_model=JudgeResponse)
async def judge(request: JudgeRequest):
    """多模态告警复判

    示例：
        POST /api/v1/judge
        {
            "image_base64": "data:image/jpeg;base64,...",
            "yolo_result": {"class": "no_helmet", "confidence": 0.87}
        }
    """
    t0 = time.time()
    logger.info(f"[Judge] yolo={request.yolo_result}")

    try:
        client = get_vlm_client()
        result = client.judge_image(
            image_base64=request.image_base64,
            yolo_result=request.yolo_result,
            prompt=request.prompt or "请判断图中人员是否存在以下行为：抽烟、未戴安全帽、接打电话、未戴口罩。",
        )

        elapsed_ms = int((time.time() - t0) * 1000)
        return JudgeResponse(
            verdict=result["verdict"],
            reasoning=result["reasoning"],
            confidence=result["confidence"],
            elapsed_ms=elapsed_ms,
        )

    except Exception as e:
        logger.exception(f"[Judge] 处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== 入口 =====================
def main():
    import uvicorn
    server_config = CONFIG["server"]
    uvicorn.run(
        "main:app",
        host=server_config["host"],
        port=server_config["port"],
        reload=server_config["reload"],
        log_level=server_config["log_level"],
    )


if __name__ == "__main__":
    main()
