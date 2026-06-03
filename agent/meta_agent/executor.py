"""执行生成的 Agent 代码并按 RULES §2 契约判定。

不再用 stdout 字符串包含。流程：
1. 把生成的 agent_code.py 写到临时目录
2. import 之，调用 `run(user_message)` 拿到结构化 dict
3. 按 plan/tool_results 判定每条用例
"""
from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import os
import re
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _resolve_today_in(value: Any) -> Any:
    """把 expected_args_contains 中的 <TODAY> 占位符换成当前 UTC 日期。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value.replace("<TODAY>", today)
    if isinstance(value, dict):
        return {k: _resolve_today_in(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_today_in(v) for v in value]
    return value


def _import_agent_module(code: str, agent_name: str, project_root: Path):
    work_dir = Path(tempfile.mkdtemp(prefix="aoa_run_"))
    file_path = work_dir / f"{agent_name}.py"
    file_path.write_text(code, encoding="utf-8")
    if str(work_dir) not in sys.path:
        sys.path.insert(0, str(work_dir))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    spec = importlib.util.spec_from_file_location(agent_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法构造 module spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[agent_name] = mod
    spec.loader.exec_module(mod)
    return mod, file_path, work_dir


def _run_in_subprocess(code: str, user_message: str, agent_name: str,
                       project_root: str, q: mp.Queue) -> None:
    try:
        os.environ.setdefault("AOA_PROJECT_ROOT", project_root)
        mod, _, _ = _import_agent_module(code, agent_name, Path(project_root))
        result = mod.run(user_message, trace_id=str(uuid.uuid4()))
        q.put(("ok", result))
    except Exception:
        q.put(("err", traceback.format_exc()))


def _run_one_case(code: str, user_message: str, agent_name: str,
                  project_root: Path, timeout: int = 60) -> dict:
    q: mp.Queue = mp.Queue()
    p = mp.Process(target=_run_in_subprocess,
                   args=(code, user_message, agent_name, str(project_root), q))
    p.start()
    p.join(timeout=timeout)
    if p.is_alive():
        p.terminate()
        p.join(5)
        return {"ok": False, "error": f"timeout after {timeout}s", "result": None}
    if q.empty():
        return {"ok": False, "error": "no result returned", "result": None}
    status, payload = q.get()
    if status == "err":
        return {"ok": False, "error": payload, "result": None}
    return {"ok": True, "error": None, "result": payload}


def _evaluate_case(case: dict, agent_output: dict | None, run_error: str | None) -> dict:
    """按 RULES §2 契约判定。"""
    detail = {
        "input": case.get("input"),
        "expected_tool": case.get("expected_tool"),
        "expected_args_contains": case.get("expected_args_contains"),
        "expected_output_contains": case.get("expected_output_contains"),
        "passed": False,
        "reasons": [],
        "agent_output_preview": "",
        "plan": [],
        "tool_results_preview": [],
    }
    if run_error or agent_output is None:
        detail["reasons"].append(f"runtime error: {run_error or 'no output'}")
        return detail

    plan = agent_output.get("plan") or []
    detail["plan"] = plan
    detail["tool_results_preview"] = [
        {"task": tr.get("task"), "args": tr.get("args"), "error": tr.get("error")}
        for tr in (agent_output.get("tool_results") or [])
    ]
    detail["agent_output_preview"] = (agent_output.get("response") or "")[:300]

    if agent_output.get("error"):
        detail["reasons"].append(f"agent reported error: {agent_output['error']}")

    expected_tool = case.get("expected_tool")
    if expected_tool:
        called = [p.get("task") for p in plan]
        if expected_tool not in called:
            detail["reasons"].append(
                f"expected_tool not in plan: expected={expected_tool}, got={called}"
            )

    expected_args = _resolve_today_in(case.get("expected_args_contains") or {})
    if expected_args and isinstance(expected_args, dict) and expected_tool:
        # 找到第一个匹配 expected_tool 的 plan step
        match = next((p for p in plan if p.get("task") == expected_tool), None)
        if match is None:
            pass  # 上面已经记
        else:
            actual_args = match.get("args") or {}
            for k, v in expected_args.items():
                if k not in actual_args:
                    detail["reasons"].append(f"missing arg: {k}")
                elif str(actual_args[k]).strip().lower() != str(v).strip().lower():
                    detail["reasons"].append(
                        f"arg mismatch: {k}: expected={v}, got={actual_args[k]}"
                    )

    expected_substr = _resolve_today_in(case.get("expected_output_contains") or "")
    if expected_substr:
        haystack = (agent_output.get("response") or "")
        # tool_results 里的内容也算
        haystack += " " + json.dumps(agent_output.get("tool_results") or [], ensure_ascii=False)
        if expected_substr not in haystack:
            # 中英不敏感再试一次
            if expected_substr.lower() not in haystack.lower():
                detail["reasons"].append(f"output missing substring: {expected_substr!r}")

    detail["passed"] = (
        not detail["reasons"]
        and bool(plan)
        and not agent_output.get("error")
    )
    return detail


class Executor:
    """生成代码执行器（Process 隔离 + plan-based 判定）。"""

    def __init__(self, timeout: int = 60, project_root: Path | None = None):
        self.timeout = timeout
        self.project_root = project_root or Path(__file__).resolve().parent.parent

    def run_tests(self, code: str, test_cases: list[dict],
                  agent_name: str = "generated_agent") -> dict:
        if not test_cases:
            return {"passed": 0, "failed": 0, "details": [], "error": "no test_cases"}

        details = []
        passed = 0
        failed = 0
        for case in test_cases:
            run_out = _run_one_case(code, case.get("input", ""), agent_name,
                                    self.project_root, timeout=self.timeout)
            detail = _evaluate_case(case, run_out["result"], run_out["error"])
            details.append(detail)
            if detail["passed"]:
                passed += 1
            else:
                failed += 1

        return {
            "passed": passed,
            "failed": failed,
            "total": len(test_cases),
            "success_rate": passed / len(test_cases),
            "details": details,
            "error": None,
        }


if __name__ == "__main__":
    # 烟测：用极简 agent code 验证执行器逻辑
    fake = '''
def run(user_message, **ctx):
    return {"response": f"echo: {user_message}",
            "plan": [{"task": "query_alarms", "args": {"date": "2026-06-01"}, "reason": "x"}],
            "tool_results": [{"task": "query_alarms", "args": {"date": "2026-06-01"}, "result": {"total": 5}, "error": None}],
            "error": None, "trace_id": "x"}
'''
    ex = Executor(timeout=10)
    out = ex.run_tests(fake, [
        {"input": "今天的告警", "expected_tool": "query_alarms",
         "expected_args_contains": {"date": "2026-06-01"}},
    ], agent_name="fake_agent")
    print(json.dumps(out, ensure_ascii=False, indent=2))
