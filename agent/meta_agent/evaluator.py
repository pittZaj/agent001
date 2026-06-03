"""按 spec 的 acceptance 段读取权重/阈值，计算指标。"""
from __future__ import annotations

from typing import Any


class Evaluator:
    """权重不再写死；从 spec.acceptance 读取（支持任务覆盖）。"""

    DEFAULT_WEIGHTS = {"tool_accuracy": 0.3, "execution_success": 0.2, "case_pass_rate": 0.5}

    def __init__(self, acceptance: dict[str, float] | None = None,
                 weights: dict[str, float] | None = None):
        self.acceptance = acceptance or {}
        self.weights = weights or self.DEFAULT_WEIGHTS

    def evaluate(self, test_result: dict[str, Any]) -> dict[str, float]:
        if test_result.get("error"):
            return {"tool_accuracy": 0.0, "execution_success": 0.0, "overall_score": 0.0}

        details = test_result.get("details", [])
        if not details:
            return {"tool_accuracy": 0.0, "execution_success": 0.0, "overall_score": 0.0}

        total = len(details)
        passed = sum(1 for d in details if d.get("passed"))

        # tool_accuracy: 期望工具确实出现在 plan 中的比例
        tool_hit = 0
        for d in details:
            expected = d.get("expected_tool")
            if not expected:
                tool_hit += 1
                continue
            tools_in_plan = [p.get("task") for p in d.get("plan", [])]
            if expected in tools_in_plan:
                tool_hit += 1
        tool_accuracy = tool_hit / total

        # execution_success: 没有"runtime error" / "agent reported error"
        exec_ok = 0
        for d in details:
            reasons = d.get("reasons", [])
            if not any(r.startswith("runtime error") or r.startswith("agent reported error") for r in reasons):
                exec_ok += 1
        execution_success = exec_ok / total

        overall = (
            tool_accuracy * self.weights.get("tool_accuracy", 0.3)
            + execution_success * self.weights.get("execution_success", 0.2)
            + (passed / total) * self.weights.get("case_pass_rate", 0.5)
        )
        return {
            "tool_accuracy": round(tool_accuracy, 3),
            "execution_success": round(execution_success, 3),
            "case_pass_rate": round(passed / total, 3),
            "overall_score": round(overall, 3),
        }

    def meets_threshold(self, metrics: dict[str, float]) -> bool:
        for key, threshold in self.acceptance.items():
            if key not in metrics:
                continue
            if metrics[key] < threshold:
                return False
        return True
