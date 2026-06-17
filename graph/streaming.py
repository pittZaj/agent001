"""流式输出桥接层（SSE / Gradio 共用）。

设计背景与权衡（重要）：
    主图节点是**同步**函数、内部用 `llm.invoke()`。实测 LangGraph 原生
    `stream_mode="messages"` 在这种"同步节点 + invoke"下拿不到任何 token
    （回调上下文跨不过同步边界，会得到 0 个 token chunk）。若把整图改异步，
    又会与既有的 nest_asyncio / 跨事件循环 / 线程本地 MCP 连接等约定冲突，风险高。

    因此这里用「线程 worker + 队列桥接 + contextvar emitter」的低侵入方案：
      - graph 仍在工作线程里同步跑（沿用 _run_async 的线程模型，不动）；
      - 节点通过 contextvar 拿到当前请求的 emitter，把"进度/逐字"事件投入队列；
      - SSE 端 / Gradio 端在异步侧排空队列，实时下发。
    非流式调用（graph.invoke）时 emitter 为空，所有 emit_* 均为 no-op，零影响。

事件类型（投入队列的统一结构 {"event": <type>, "data": {...}}）：
    - status : 进度事件 {stage, message}   —— 覆盖慢的取数阶段，全链路可见
    - token  : 增量答案文本 {text}          —— 仅 LLM 真正逐字生成答案时
    - done   : 最终完整结果 {response, modality, plan, tool_calls, elapsed_ms}
    - error  : {detail}
"""
from __future__ import annotations

import contextvars
import queue
from typing import Any, Dict, Iterable, Optional

# 当前请求的流式发射器（仅在流式请求里被设置；非流式为 None）
_current_emitter: contextvars.ContextVar[Optional["StreamEmitter"]] = contextvars.ContextVar(
    "ksagent_stream_emitter", default=None
)

# 队列结束哨兵
_SENTINEL = object()


class StreamEmitter:
    """线程安全的事件发射器：节点线程投递，异步侧排空。"""

    def __init__(self) -> None:
        self._q: "queue.Queue[Any]" = queue.Queue()

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        self._q.put({"event": event, "data": data})

    def close(self) -> None:
        self._q.put(_SENTINEL)

    def get(self, timeout: Optional[float] = None):
        """取一条事件；遇哨兵返回 _SENTINEL。供异步侧在 executor 里阻塞调用。"""
        return self._q.get(timeout=timeout)


# ===================== 节点侧 API（被 graph/nodes.py 调用）=====================
def set_emitter(emitter: Optional[StreamEmitter]) -> contextvars.Token:
    """在工作线程入口设置当前 emitter，返回 token 以便复位。"""
    return _current_emitter.set(emitter)


def reset_emitter(token: contextvars.Token) -> None:
    _current_emitter.reset(token)


def get_emitter() -> Optional[StreamEmitter]:
    return _current_emitter.get()


def emit_status(stage: str, message: str) -> None:
    """投递一条进度事件（非流式请求下自动 no-op）。"""
    em = _current_emitter.get()
    if em is not None:
        em.emit("status", {"stage": stage, "message": message})


def emit_token(text: str) -> None:
    """投递一条增量答案文本（非流式请求下自动 no-op）。"""
    if not text:
        return
    em = _current_emitter.get()
    if em is not None:
        em.emit("token", {"text": text})


def stream_llm(llm, messages) -> str:
    """统一的"答案 LLM"调用：流式拿 token 并逐字 emit，返回拼接后的完整文本。

    - 流式请求：每个 chunk 即时 emit_token，前端逐字显示；
    - 非流式请求：emitter 为空，emit_token 静默，等价于一次性 invoke 后返回全文。
    这样节点代码只此一处，无需区分流式/非流式两套分支。
    """
    parts: list[str] = []
    for chunk in llm.stream(messages):
        piece = getattr(chunk, "content", "") or ""
        if isinstance(piece, list):  # 兼容多模态分块返回（取文本片段）
            piece = "".join(
                seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in piece
            )
        if piece:
            parts.append(piece)
            emit_token(piece)
    return "".join(parts)


# 工具名 → 友好中文进度文案（status 事件用）
TOOL_LABELS: Dict[str, str] = {
    "ai_event_list": "查询平台 AI 告警",
    "ai_event_detail": "获取告警详情",
    "ai_event_deal": "回写告警状态",
    "aggregate_alarms": "聚合统计告警",
    "visualize_alarms": "生成可视化图表",
    "vlm_judge_alarm": "VLM 复判告警",
    "update_alarm_status": "回写复核状态",
    "fetch_alarm_context": "回溯录像上下文",
    "kb_regulation": "检索规章制度知识库",
    "video_device_list": "查询视频设备",
    "direct_response": "组织回复",
}


def tool_label(tool: str) -> str:
    return TOOL_LABELS.get(tool, tool)


# ===================== 异步侧：把一次图执行变成事件流 =====================
import asyncio  # noqa: E402
import time  # noqa: E402
import threading  # noqa: E402

from loguru import logger  # noqa: E402


async def run_graph_stream(graph, initial_state: Dict[str, Any], *, modality: str, thread_id: str = ""):
    """把一次（同步）图执行驱动为 SSE 友好的异步事件生成器。

    工作线程内设置 emitter 并调用 graph.invoke()；本协程在事件循环里用
    run_in_executor 阻塞排空队列，逐条 yield {"event","data"}。最后补一条
    done（含完整 final_response 与 plan/tool_calls），与非流式响应同构。

    thread_id：短期记忆会话键。非空时通过 config 传给图，checkpointer 据此
    加载/保存该会话历史，实现多轮记忆；为空则按无记忆的一次性调用执行。
    """
    emitter = StreamEmitter()
    result_box: Dict[str, Any] = {}
    err_box: Dict[str, Any] = {}
    t0 = time.time()

    config = {"configurable": {"thread_id": thread_id}} if thread_id else None

    def _worker():
        token = set_emitter(emitter)
        try:
            if config is not None:
                result_box["state"] = graph.invoke(initial_state, config=config)
            else:
                result_box["state"] = graph.invoke(initial_state)
        except Exception as e:  # noqa: BLE001
            logger.exception("[stream] 图执行失败")
            err_box["detail"] = f"{type(e).__name__}: {e}"
        finally:
            reset_emitter(token)
            emitter.close()  # 投递哨兵，通知排空侧结束

    threading.Thread(target=_worker, name="ksagent-graph-stream", daemon=True).start()

    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, emitter.get)
        if item is _SENTINEL:
            break
        yield item

    if err_box:
        yield {"event": "error", "data": {"detail": err_box["detail"]}}
        return

    state = result_box.get("state", {})
    yield {
        "event": "done",
        "data": {
            "response": state.get("final_response", ""),
            "modality": modality,
            "plan": state.get("plan", []),
            "tool_calls": state.get("tool_results", []),
            "elapsed_ms": int((time.time() - t0) * 1000),
        },
    }
