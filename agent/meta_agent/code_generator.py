"""生成 LangGraph Agent 的 Python 代码。

模板部分（StateGraph + 三节点）保持稳定；`SYSTEM_PROMPT` 注入由 Claude 生成的内容。
工具调用 **不再 mock**：直接 import meta_agent.tool_impl.call_tool 真连 SQLite。

输出契约严格符合 RULES §1：
    {"response": str, "plan": list, "tool_results": list, "error": str|None, "trace_id": str}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


CODE_TEMPLATE = '''"""自动生成的 LangGraph Agent: {agent_name}

由 meta_agent.code_generator 在 {generated_at} 生成。
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
_PROJECT_ROOT = Path(os.environ.get("AOA_PROJECT_ROOT", "{project_root}"))
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
SYSTEM_PROMPT = {system_prompt_literal}


# -------------------- LLM 配置（执行 Agent 的小模型）--------------------
LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "{llm_base_url}")
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "{llm_model}")
LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "EMPTY")
DATA_SOURCE = "{data_source}"
AGENT_NAME = "{agent_name}"
ALLOWED_TOOLS = {allowed_tools_literal}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_today_placeholders(args: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(args)
    for k, v in out.items():
        if isinstance(v, str) and v.strip().lower() in ("today", "今天", "<today>"):
            out[k] = _today_utc()
    return out


def _extract_json(text: str) -> Dict[str, Any]:
    """从 LLM 输出里抽 JSON：先去 markdown 围栏，再尝试 loads，再抓第一个 {{...}} 块。"""
    if not text:
        return {{}}
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\\s*(.+?)```", s, flags=re.DOTALL)
        if m:
            s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\\{{.*\\}}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {{}}
    return {{}}


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

    today_hint = f"\\n注意：当前 UTC 日期是 {{_today_utc()}}。"
    llm = ChatOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY,
                     model=LLM_MODEL, temperature=0.2, max_tokens=1024)
    try:
        resp = llm.invoke([
            {{"role": "system", "content": SYSTEM_PROMPT + today_hint}},
            {{"role": "user", "content": user}},
        ])
        data = _extract_json(getattr(resp, "content", "") or "")
        plan = data.get("plan", [])
        if not isinstance(plan, list):
            plan = []
        # 过滤幻觉工具
        plan = [p for p in plan if isinstance(p, dict) and p.get("task") in ALLOWED_TOOLS]
        # 解析 today 占位
        for p in plan:
            p["args"] = _resolve_today_placeholders(p.get("args", {{}}) or {{}})
        state["plan"] = plan
        if not plan:
            state["error"] = "planner_no_valid_plan"
    except Exception as e:
        state["error"] = f"planner_exception: {{type(e).__name__}}: {{e}}"
    return state


def _executor_node(state: AgentState) -> AgentState:
    plan = state.get("plan", [])
    idx = state.get("current_task_idx", 0)
    if idx >= len(plan):
        return state
    step = plan[idx]
    tool_name = step.get("task")
    args = step.get("args", {{}}) or {{}}
    ctx = {{"trace_id": state.get("trace_id", ""), "agent_name": AGENT_NAME}}
    result = call_tool(tool_name, args, ctx=ctx, data_source=DATA_SOURCE)
    state["tool_results"].append({{
        "task": tool_name,
        "args": args,
        "result": result if not result.get("error") else None,
        "error": result.get("error"),
    }})
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
        state["final_response"] = f"执行失败: {{state['error']}}"
        return state
    parts = [f"为查询「{{state['user_message']}}」, 我执行了以下步骤："]
    for i, tr in enumerate(state.get("tool_results", []), 1):
        if tr.get("error"):
            parts.append(f"{{i}}. {{tr['task']}}({{tr['args']}}) 失败: {{tr['error']}}")
            continue
        res = tr.get("result") or {{}}
        if tr["task"] == "query_alarms":
            total = res.get("total", 0)
            by_type = res.get("by_type", [])
            summary = ", ".join(f"{{x['alarm_type']}}={{x['count']}}" for x in by_type[:5])
            parts.append(f"{{i}}. query_alarms: 共 {{total}} 条告警 [{{summary}}]")
        else:
            parts.append(f"{{i}}. {{tr['task']}}: {{json.dumps(res, ensure_ascii=False)[:200]}}")
    state["final_response"] = "\\n".join(parts)
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
                            {{"executor": "executor", "formatter": "formatter"}})
    g.add_conditional_edges("executor", _should_continue,
                            {{"execute": "executor", "format": "formatter"}})
    g.add_edge("formatter", END)
    return g.compile()


_GRAPH = None


def run(user_message: str, **ctx) -> Dict[str, Any]:
    """RULES §1 契约入口。"""
    global _GRAPH
    trace_id = ctx.get("trace_id") or str(uuid.uuid4())
    if user_message.strip() == "__healthcheck__":
        return {{"response": "ok", "plan": [], "tool_results": [], "error": None, "trace_id": trace_id}}
    if _GRAPH is None:
        _GRAPH = _build_graph()
    if _GRAPH is None:
        return {{"response": "graph not available", "plan": [], "tool_results": [],
                 "error": "langgraph not installed", "trace_id": trace_id}}
    init: AgentState = {{
        "user_message": user_message,
        "plan": [],
        "current_task_idx": 0,
        "tool_results": [],
        "final_response": "",
        "error": "",
        "trace_id": trace_id,
    }}
    final = _GRAPH.invoke(init)
    return {{
        "response": final.get("final_response", ""),
        "plan": final.get("plan", []),
        "tool_results": final.get("tool_results", []),
        "error": final.get("error") or None,
        "trace_id": trace_id,
    }}


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "今天的告警"
    out = run(msg)
    print(json.dumps(out, ensure_ascii=False, indent=2))
'''


class CodeGenerator:
    """LangGraph Agent 代码生成器（模板填充式）。"""

    def __init__(self,
                 llm_base_url: str = "http://127.0.0.1:8004/v1",
                 llm_model: str = "Qwen3-VL-4B-Instruct-FP8"):
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model

    def generate(self, system_prompt: str, tools: list[dict[str, Any]],
                 agent_name: str = "generated_agent",
                 data_source: str = "sqlite:data/ksipms_dev.db",
                 project_root: str = "") -> str:
        allowed = [t.get("name") for t in tools if t.get("name")]
        from pathlib import Path as _P
        if not project_root:
            project_root = str(_P(__file__).resolve().parents[1])
        return CODE_TEMPLATE.format(
            agent_name=agent_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            system_prompt_literal=_py_str_literal(system_prompt),
            llm_base_url=self.llm_base_url,
            llm_model=self.llm_model,
            data_source=data_source,
            allowed_tools_literal=repr(set(allowed)),
            project_root=project_root,
        )


def _py_str_literal(s: str) -> str:
    """把任意字符串安全地嵌入 Python 三引号字符串。"""
    safe = s.replace("\\", "\\\\").replace('"""', '"\\"\\""')
    return '"""\n' + safe + '\n"""'


if __name__ == "__main__":
    g = CodeGenerator()
    code = g.generate(
        system_prompt="你是 alarm_query_agent。\n规则：必须输出 plan JSON。",
        tools=[{"name": "query_alarms", "description": "x", "parameters": {"date": "YYYY-MM-DD"}}],
        agent_name="alarm_query_agent",
    )
    print(code[:1500])
