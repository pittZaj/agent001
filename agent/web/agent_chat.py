"""调用已发布 Agent 进行对话测试的后端。

设计要点：
- 每条消息独立调用 agent.run（agent 本身无会话状态）
- UI 用 ChatGPT 风格只是展示，多轮上下文不会自动喂给 agent
- agent 模块加载后用模块名缓存，避免重复 import
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from registry import list_agents, load_agent_run  # noqa: E402

_RUN_CACHE: dict[str, Any] = {}


def published_agent_names() -> list[str]:
    return list(list_agents().keys())


def _get_run(name: str):
    if name in _RUN_CACHE:
        return _RUN_CACHE[name]
    run_fn = load_agent_run(name)
    _RUN_CACHE[name] = run_fn
    return run_fn


def chat_once(agent_name: str, message: str) -> dict[str, Any]:
    """单轮调用：返回 {response, plan, tool_results, error, trace_id, elapsed_ms}。"""
    if not agent_name:
        return {"response": "(未选择 Agent)", "plan": [], "tool_results": [],
                "error": "no agent selected", "trace_id": "",
                "elapsed_ms": 0}
    if not message or not message.strip():
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
    _RUN_CACHE.pop(name, None)
    try:
        _get_run(name)
        return f"✅ 重新加载 {name}"
    except Exception:
        return f"❌ 加载失败:\n{traceback.format_exc()}"
