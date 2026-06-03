"""分析失败原因 → 给 PromptGenerator.optimize 用的反馈文本。

走 Claude（如有 client），输出给 Claude 的内容是结构化失败摘要而非整段日志，节省 token。
无 client 时降级为关键词聚合。
"""
from __future__ import annotations

import textwrap
from collections import Counter
from typing import Any

from .llm_client import ClaudeClient


META_PROMPT_FEEDBACK = textwrap.dedent("""
    You are a system prompt debugger. Read failure details and produce concise, actionable feedback
    for the prompt-engineer to rewrite the system prompt.

    Output rules:
    - Output Chinese (Simplified).
    - Use bullet points; max 6 bullets.
    - Be specific: name the parameter / tool / format that went wrong.
    - Do NOT propose a full new prompt; just describe what to fix.
""").strip()


class FeedbackAnalyzer:
    def __init__(self, client: ClaudeClient | None = None):
        self.client = client

    def analyze(self, test_result: dict[str, Any]) -> str:
        details = test_result.get("details", [])
        failed = [d for d in details if not d.get("passed")]
        if not failed:
            return "all tests passed"

        if self.client is None:
            return self._fallback_summary(failed)

        bullet_in = []
        for i, d in enumerate(failed, 1):
            bullet_in.append(textwrap.dedent(f"""
                Case {i}:
                  input: {d.get('input')!r}
                  expected_tool: {d.get('expected_tool')}
                  expected_args_contains: {d.get('expected_args_contains')}
                  actual_plan: {d.get('plan')}
                  reasons: {d.get('reasons')}
            """).strip())

        user = "Failures:\n\n" + "\n\n".join(bullet_in)
        return self.client.call(
            system=META_PROMPT_FEEDBACK,
            user=user,
            max_tokens=800,
            temperature=0.3,
            tag="feedback_analyze",
        ).strip()

    @staticmethod
    def _fallback_summary(failed: list[dict]) -> str:
        c = Counter()
        for d in failed:
            for r in d.get("reasons", []):
                head = r.split(":")[0].strip()
                c[head] += 1
        lines = ["失败模式聚合："]
        for head, n in c.most_common():
            lines.append(f"- {head}: {n} 次")
        return "\n".join(lines)
