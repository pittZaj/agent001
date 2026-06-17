"""调用已发布 Agent 进行对话测试的后端。

设计要点：
- 每条消息独立调用 agent.run（agent 本身无会话状态）
- UI 用 ChatGPT 风格只是展示，多轮上下文不会自动喂给 agent
- agent 模块加载后用模块名缓存，避免重复 import
- 支持动态刷新已注册 agent 列表和重新加载 agent 模块
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

# 迁移说明（2026-06-15）：本文件从 agent/agent/web/ 迁移到 agent/web/。
# 路径层级变化：
#   - LANGGRAPH_ROOT = parents[1] = agent/（含 skills/ graph/ config.yaml utils/）
#   - AOA_ROOT       = agent/agent/（含 registry.py、artifacts/、已发布 agent 代码）
# PROJECT_ROOT 仍指向 Agent-of-Agent 层（agent/agent/），保持 registry/artifacts 引用不变；
# 这样 _get_main_graph 里的 PROJECT_ROOT.parent 自动等于 agent/，skills/graph 也能正确 import。
LANGGRAPH_ROOT = Path(__file__).resolve().parents[1]   # agent/
PROJECT_ROOT = LANGGRAPH_ROOT / "agent"                # agent/agent/（registry 所在）
for _p in (str(LANGGRAPH_ROOT), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from registry import list_agents, load_agent_run  # noqa: E402

_RUN_CACHE: dict[str, Any] = {}

# 主智能体：增强后的 Plan-Execute 主图（支持复判/统计/可视化/回溯/回写）
# 用户在 Tab6 默认只需选它，背后走升级后的主图。
MAIN_AGENT_NAME = "主智能体(增强)"
_MAIN_GRAPH = None


def _get_main_graph():
    """懒加载主图（含 Skill Registry 初始化）"""
    global _MAIN_GRAPH
    if _MAIN_GRAPH is None:
        import asyncio
        # agent 项目根目录（agent/），主图代码在此
        agent_root = PROJECT_ROOT.parent
        if str(agent_root) not in sys.path:
            sys.path.insert(0, str(agent_root))
        from skills.init import init_skill_registry
        from graph import get_graph
        asyncio.run(init_skill_registry())
        _MAIN_GRAPH = get_graph()
    return _MAIN_GRAPH


def _run_main_agent(message: str, trace_id: str = "", images: list[str] | None = None,
                    thread_id: str = "") -> dict[str, Any]:
    """调用增强主图，返回与 generated agent 一致的 dict 结构。

    images: 图片 base64 data URL 列表（可空）。非空时主图走 VLM 多模态对话分支。
    thread_id: 短期记忆会话键。非空时 checkpointer 按它加载/保存历史，实现多轮记忆。
    """
    graph = _get_main_graph()
    state = {
        "session_id": thread_id or trace_id or "web", "user_message": message,
        "images": images or [],
        "plan": [], "current_task_idx": 0, "tool_results": [],
        "step_outputs": {}, "final_response": "", "error": None, "messages": [],
    }
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    out = graph.invoke(state, config=config) if config else graph.invoke(state)
    return {
        "response": out.get("final_response", ""),
        "plan": out.get("plan", []),
        "tool_results": out.get("tool_results", []),
        "error": out.get("error"),
        "trace_id": trace_id,
    }


def published_agent_names() -> list[str]:
    """获取可选 Agent 列表：主智能体始终排首位，其后是已发布的生成式 agent"""
    return [MAIN_AGENT_NAME] + list(list_agents().keys())


def session_turn_info(thread_id: str) -> tuple[int, bool]:
    """返回某会话 (已完成轮数, 是否达到软上限需提示新建会话)。

    从 checkpointer 读该 thread_id 的已存 messages 计 AI 回复条数。
    读不到（新会话/未初始化）按 0 轮处理。
    """
    if not thread_id:
        return 0, False
    try:
        from graph.memory import get_checkpointer, count_turns, SOFT_TURN_LIMIT
        graph = _get_main_graph()
        cfg = {"configurable": {"thread_id": thread_id}}
        snap = graph.get_state(cfg)
        msgs = (snap.values or {}).get("messages", []) if snap else []
        turns = count_turns(msgs)
        return turns, turns >= SOFT_TURN_LIMIT
    except Exception:
        return 0, False


def _stream_main_agent(message: str, images: list[str], thread_id: str = ""):
    """主图流式驱动：工作线程跑 graph + emitter，本生成器同步排空队列逐步产出。

    展示策略：进度事件累积成"状态行"显示在顶部（引用块），LLM token 实时拼到
    答案区；done 时用完整 response 覆盖（确保统计/画图路径的内嵌图表也能渲染）。
    thread_id：短期记忆会话键，非空时按它加载/保存历史。
    """
    import threading
    import time as _time
    from graph.streaming import StreamEmitter, set_emitter, reset_emitter, _SENTINEL

    graph = _get_main_graph()  # 触发 Skill Registry 初始化（首次）
    trace_id = str(uuid.uuid4())
    state = {
        "session_id": thread_id or trace_id, "user_message": message, "images": images,
        "plan": [], "current_task_idx": 0, "tool_results": [],
        "step_outputs": {}, "final_response": "", "error": None, "messages": [],
    }
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None

    emitter = StreamEmitter()
    box: dict[str, Any] = {}
    t0 = _time.time()

    def _worker():
        token = set_emitter(emitter)
        try:
            box["state"] = graph.invoke(state, config=config) if config else graph.invoke(state)
        except Exception:
            box["error"] = traceback.format_exc()
        finally:
            reset_emitter(token)
            emitter.close()

    threading.Thread(target=_worker, name="ksagent-web-stream", daemon=True).start()

    status_lines: list[str] = []
    answer = ""
    while True:
        item = emitter.get()
        if item is _SENTINEL:
            break
        ev, data = item["event"], item["data"]
        if ev == "status":
            status_lines.append(data.get("message", ""))
        elif ev == "token":
            answer += data.get("text", "")
        yield _compose(status_lines, answer), None

    if box.get("error"):
        out = {"response": "", "plan": [], "tool_results": [], "error": box["error"],
               "trace_id": trace_id, "elapsed_ms": int((_time.time() - t0) * 1000)}
        yield f"❌ 执行失败:\n```\n{box['error']}\n```", out
        return

    st = box.get("state", {})
    final = st.get("final_response", "") or answer
    out = {
        "response": final, "plan": st.get("plan", []),
        "tool_results": st.get("tool_results", []), "error": st.get("error"),
        "trace_id": trace_id, "elapsed_ms": int((_time.time() - t0) * 1000),
    }
    # 最终用完整 response 覆盖（含内嵌图表 markdown），状态行收起为一行小结
    yield _compose(status_lines, final, collapsed=True), out


def _compose(status_lines: list[str], answer: str, collapsed: bool = False) -> str:
    """把进度状态行 + 答案拼成 Markdown：进度用引用块显示，答案正文在下方。"""
    parts = []
    if status_lines:
        if collapsed:
            parts.append(f"> ✅ {status_lines[-1]}")
        else:
            parts.append("> " + "\n> ".join(f"⏳ {s}" for s in status_lines))
    if answer:
        parts.append(answer)
    return "\n\n".join(parts) if parts else "⏳ 处理中…"


def _get_run(name: str):
    """获取 agent 的 run 函数（带缓存）"""
    if name == MAIN_AGENT_NAME:
        return _run_main_agent
    if name in _RUN_CACHE:
        return _RUN_CACHE[name]
    run_fn = load_agent_run(name)
    _RUN_CACHE[name] = run_fn
    return run_fn


def chat_once(agent_name: str, message: str, images: list[str] | None = None) -> dict[str, Any]:
    """单轮调用：返回 {response, plan, tool_results, error, trace_id, elapsed_ms}。

    images: 图片 base64 data URL 列表（可空）。仅主智能体支持图片；
            已发布的生成式 agent 签名固定，传图会被忽略（并给出提示）。
    """
    images = images or []
    if not agent_name:
        return {"response": "(未选择 Agent)", "plan": [], "tool_results": [],
                "error": "no agent selected", "trace_id": "",
                "elapsed_ms": 0}
    # 文本与图片至少有一个
    if (not message or not message.strip()) and not images:
        return {"response": "(空消息)", "plan": [], "tool_results": [],
                "error": "empty message", "trace_id": "",
                "elapsed_ms": 0}

    try:
        run_fn = _get_run(agent_name)
    except Exception:
        return {"response": "", "plan": [], "tool_results": [],
                "error": f"加载 agent 失败:\n{traceback.format_exc()}",
                "trace_id": "", "elapsed_ms": 0}

    t0 = time.time()
    trace_id = str(uuid.uuid4())
    try:
        if agent_name == MAIN_AGENT_NAME:
            out = run_fn(message, trace_id=trace_id, images=images)
        else:
            # 生成式 agent 不支持多模态：有图时显式提示，仍按文本调用
            if images:
                logger.warning(f"[chat] agent={agent_name} 不支持图片，已忽略 {len(images)} 张图")
            out = run_fn(message, trace_id=trace_id)
    except Exception:
        return {"response": "", "plan": [], "tool_results": [],
                "error": traceback.format_exc(), "trace_id": trace_id,
                "elapsed_ms": int((time.time() - t0) * 1000)}

    if not isinstance(out, dict):
        return {"response": str(out), "plan": [], "tool_results": [],
                "error": f"agent.run 返回非 dict: {type(out).__name__}",
                "trace_id": trace_id,
                "elapsed_ms": int((time.time() - t0) * 1000)}

    out.setdefault("response", "")
    out.setdefault("plan", [])
    out.setdefault("tool_results", [])
    out.setdefault("error", None)
    out.setdefault("trace_id", trace_id)
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


def chat_stream(agent_name: str, message: str, images: list[str] | None = None,
                thread_id: str = ""):
    """流式单轮调用：生成器，逐步 yield (partial_text, out_or_None)。

    - 主智能体：复用主图 + StreamEmitter（在工作线程跑图，本生成器同步排空队列）。
      逐字/进度实时产出；结束时给出含 plan/tool_results/耗时 的完整 out。
      thread_id 非空时按它加载/保存短期记忆，实现多轮连贯。
    - 生成式 agent：不支持流式，回退为一次性 chat_once，仅 yield 一次最终结果。

    yield 约定：
      (text, None)  → 中间增量（text 为"截至目前应显示的完整文本"，已含进度+答案）
      (text, out)   → 最终（out 为完整 dict，供 debug 面板渲染）
    """
    images = images or []
    if not agent_name:
        yield "(未选择 Agent)", {"response": "(未选择 Agent)", "error": "no agent selected",
                                 "plan": [], "tool_results": [], "trace_id": "", "elapsed_ms": 0}
        return
    if (not message or not message.strip()) and not images:
        yield "(空消息)", {"response": "(空消息)", "error": "empty message",
                          "plan": [], "tool_results": [], "trace_id": "", "elapsed_ms": 0}
        return

    # 生成式 agent：无流式能力，也不支持短期记忆，直接走一次性调用
    if agent_name != MAIN_AGENT_NAME:
        out = chat_once(agent_name, message, images=images)
        yield out.get("response", ""), out
        return

    yield from _stream_main_agent(message, images, thread_id=thread_id)


def format_debug_panel(out: dict[str, Any]) -> str:
    """把 plan / tool_results 渲染为可读的 markdown 块。"""
    if not out:
        return ""
    parts = []
    parts.append(f"**trace_id**: `{out.get('trace_id','')}`  · **耗时**: {out.get('elapsed_ms')}ms")
    if out.get("error"):
        parts.append(f"\n**❌ 错误**:\n```\n{out['error']}\n```")
    plan = out.get("plan") or []
    if plan:
        parts.append("\n**📋 plan**:\n```json\n" + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```")
    tool_results = out.get("tool_results") or []
    if tool_results:
        # 只展示前 3 条，避免单条 JSON 过长
        parts.append("\n**🔧 tool_results** (前 3 条):\n```json\n"
                     + json.dumps(tool_results[:3], ensure_ascii=False, indent=2)
                     + "\n```")
    return "\n".join(parts)


def reload_agent(name: str) -> str:
    """重新加载指定 agent 的模块（清除缓存后重新导入）

    使用场景：
    - 发布了新版本的同名 agent
    - 手动修改了 published 目录下的 agent 代码
    - 需要确保加载最新版本

    Returns:
        str: 操作结果信息
    """
    if not name:
        return "❌ 请选择一个 Agent"

    # 主智能体：重置主图缓存，下次调用会重新初始化 Skill Registry + 图
    if name == MAIN_AGENT_NAME:
        global _MAIN_GRAPH
        _MAIN_GRAPH = None
        return f"✅ 已重置 `{name}`（下次对话将重新加载 Skill Registry 和主图）"

    # 清除缓存
    _RUN_CACHE.pop(name, None)

    # 尝试重新加载
    try:
        run_fn = _get_run(name)
        return f"✅ 已重新加载 `{name}` (缓存已清除，模块已重新导入)"
    except KeyError:
        return f"❌ Agent `{name}` 不存在于注册表中"
    except FileNotFoundError as e:
        return f"❌ Agent `{name}` 的代码文件不存在: {e}"
    except Exception as e:
        return f"❌ 加载失败: {type(e).__name__}: {e}\n\n{traceback.format_exc()}"
