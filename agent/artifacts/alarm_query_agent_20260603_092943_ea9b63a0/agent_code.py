"""自动生成的 LangGraph Agent: alarm_query_agent

由 meta_agent.code_generator 在 2026-06-03T01:29:45.019369+00:00 生成。
不要手改本文件——下一次生成会覆盖。
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, TypedDict

# 让生成的 agent 能 import meta_agent.tool_impl（项目根在生成时硬编码，运行时可被 AOA_PROJECT_ROOT 覆盖）
_PROJECT_ROOT = Path(os.environ.get("AOA_PROJECT_ROOT", "/mnt/data3/clip/LangGraph/agent/agent"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from meta_agent.tool_impl import call_tool  # type: ignore  # noqa: E402

try:
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, END
except ImportError as e:  # 兜底：测试时如果没装也要能 import 本文件
    ChatOpenAI = None  # type: ignore
    StateGraph = None  # type: ignore
    END = None  # type: ignore


# -------------------- 状态 --------------------
class AgentState(TypedDict):
    user_message: str
    plan: List[Dict[str, Any]]
    current_task_idx: int
    tool_results: List[Dict[str, Any]]
    final_response: str
    error: str
    trace_id: str


# -------------------- System Prompt（Claude 生成）--------------------
SYSTEM_PROMPT = """
你是一个任务执行智能体：alarm_query_agent。
目标：查询安全生产平台的告警记录：按日期/类型/摄像头筛选，并按类型统计数量。
可用工具：query_alarms。
必须以 JSON 输出：{"plan":[{"task":"<tool>","args":{},"reason":"..."}]}
禁止使用列表外的工具。
"""


# -------------------- LLM 配置（执行 Agent 的小模型）--------------------
LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "http://127.0.0.1:8004/v1")
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "Qwen3-VL-4B-Instruct-FP8")
LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "EMPTY")
DATA_SOURCE = "sqlite:data/ksipms_dev.db.alarms"
AGENT_NAME = "alarm_query_agent"
ALLOWED_TOOLS = {'query_alarms'}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_today_placeholders(args: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(args)
    for k, v in out.items():
        if isinstance(v, str) and v.strip().lower() in ("today", "今天", "<today>"):
            out[k] = _today_utc()
    return out


def _extract_json(text: str) -> Dict[str, Any]:
    """从 LLM 输出里抽 JSON：先去 markdown 围栏，再尝试 loads，再抓第一个 {...} 块。"""
    if not text:
        return {}
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(.+?)```", s, flags=re.DOTALL)
        if m:
            s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


# -------------------- 节点 --------------------
def _planner_node(state: AgentState) -> AgentState:
    user = state["user_message"]
    if user.strip() == "__healthcheck__":
        state["plan"] = []
        state["final_response"] = "ok"
        return state
    if ChatOpenAI is None:
        state["error"] = "langchain_openai not installed"
        return state

    today_hint = f"\n注意：当前 UTC 日期是 {_today_utc()}。"
    llm = ChatOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY,
                     model=LLM_MODEL, temperature=0.2, max_tokens=1024)
    try:
        resp = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT + today_hint},
            {"role": "user", "content": user},
        ])
        data = _extract_json(getattr(resp, "content", "") or "")
        plan = data.get("plan", [])
        if not isinstance(plan, list):
            plan = []
        # 过滤幻觉工具
        plan = [p for p in plan if isinstance(p, dict) and p.get("task") in ALLOWED_TOOLS]
        # 解析 today 占位
        for p in plan:
            p["args"] = _resolve_today_placeholders(p.get("args", {}) or {})
        state["plan"] = plan
        if not plan:
            state["error"] = "planner_no_valid_plan"
    except Exception as e:
        state["error"] = f"planner_exception: {type(e).__name__}: {e}"
    return state


def _executor_node(state: AgentState) -> AgentState:
    plan = state.get("plan", [])
    idx = state.get("current_task_idx", 0)
    if idx >= len(plan):
        return state
    step = plan[idx]
    tool_name = step.get("task")
    args = step.get("args", {}) or {}
    ctx = {"trace_id": state.get("trace_id", ""), "agent_name": AGENT_NAME}
    result = call_tool(tool_name, args, ctx=ctx, data_source=DATA_SOURCE)
    state["tool_results"].append({
        "task": tool_name,
        "args": args,
        "result": result if not result.get("error") else None,
        "error": result.get("error"),
    })
    state["current_task_idx"] = idx + 1
    return state


def _should_continue(state: AgentState) -> str:
    if state.get("error"):
        return "format"
    if state.get("current_task_idx", 0) >= len(state.get("plan", [])):
        return "format"
    return "execute"


def _formatter_node(state: AgentState) -> AgentState:
    if state["user_message"].strip() == "__healthcheck__":
        state["final_response"] = "ok"
        return state
    if state.get("error") and not state.get("tool_results"):
        state["final_response"] = f"执行失败: {state['error']}"
        return state
    parts = [f"为查询「{state['user_message']}」, 我执行了以下步骤："]
    for i, tr in enumerate(state.get("tool_results", []), 1):
        if tr.get("error"):
            parts.append(f"{i}. {tr['task']}({tr['args']}) 失败: {tr['error']}")
            continue
        res = tr.get("result") or {}
        if tr["task"] == "query_alarms":
            total = res.get("total", 0)
            by_type = res.get("by_type", [])
            summary = ", ".join(f"{x['alarm_type']}={x['count']}" for x in by_type[:5])
            parts.append(f"{i}. query_alarms: 共 {total} 条告警 [{summary}]")
        else:
            parts.append(f"{i}. {tr['task']}: {json.dumps(res, ensure_ascii=False)[:200]}")
    state["final_response"] = "\n".join(parts)
    return state


def _build_graph():
    if StateGraph is None:
        return None
    g = StateGraph(AgentState)
    g.add_node("planner", _planner_node)
    g.add_node("executor", _executor_node)
    g.add_node("formatter", _formatter_node)
    g.set_entry_point("planner")
    g.add_conditional_edges("planner", lambda s: "executor" if not s.get("error") and s.get("plan") else "formatter",
                            {"executor": "executor", "formatter": "formatter"})
    g.add_conditional_edges("executor", _should_continue,
                            {"execute": "executor", "format": "formatter"})
    g.add_edge("formatter", END)
    return g.compile()


_GRAPH = None


def run(user_message: str, **ctx) -> Dict[str, Any]:
    """RULES §1 契约入口。"""
    global _GRAPH
    trace_id = ctx.get("trace_id") or str(uuid.uuid4())
    if user_message.strip() == "__healthcheck__":
        return {"response": "ok", "plan": [], "tool_results": [], "error": None, "trace_id": trace_id}
    if _GRAPH is None:
        _GRAPH = _build_graph()
    if _GRAPH is None:
        return {"response": "graph not available", "plan": [], "tool_results": [],
                 "error": "langgraph not installed", "trace_id": trace_id}
    init: AgentState = {
        "user_message": user_message,
        "plan": [],
        "current_task_idx": 0,
        "tool_results": [],
        "final_response": "",
        "error": "",
        "trace_id": trace_id,
    }
    final = _GRAPH.invoke(init)
    return {
        "response": final.get("final_response", ""),
        "plan": final.get("plan", []),
        "tool_results": final.get("tool_results", []),
        "error": final.get("error") or None,
        "trace_id": trace_id,
    }


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "今天的告警"
    out = run(msg)
    print(json.dumps(out, ensure_ascii=False, indent=2))
