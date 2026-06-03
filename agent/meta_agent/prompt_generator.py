"""通过 Claude 生成 LangGraph Agent 的 system prompt。

- 不再硬编码模板，而是把 spec 与"如何写好 LangGraph Agent prompt"的元提示一起送给 Claude
- 失败重写策略：把上一轮 prompt + 失败摘要给 Claude，让它**重写**整段 prompt（不是追加）
"""
from __future__ import annotations

import json
import textwrap
from typing import Any

from .llm_client import ClaudeClient


META_PROMPT_GENERATE = textwrap.dedent("""
    You are an expert prompt engineer designing system prompts for LangGraph Agents.

    Output rules:
    - Output ONLY the system prompt text. No preamble, no markdown fences, no commentary.
    - Write in Chinese (Simplified) because users are Chinese.
    - The prompt MUST tell the agent to output a JSON object with `plan` field.
    - `plan` is a list of `{"task": <tool_name>, "args": <object>, "reason": <string>}`.
    - Tool names MUST be exactly from the provided tools list. Reject hallucinated tools.
    - For date arguments use `YYYY-MM-DD` format; "today" must be resolved by the agent caller (LLM doesn't know today).
    - Cover: when to call the tool, how to extract args from user input (with synonym mapping if relevant), what to do if input is ambiguous.
    - Keep it concise (under 600 Chinese characters).
""").strip()


META_PROMPT_OPTIMIZE = textwrap.dedent("""
    You are an expert prompt engineer. The previous system prompt produced failures on test cases.

    Output rules:
    - Output ONLY the rewritten full system prompt. Do NOT just append rules.
    - Write in Chinese (Simplified).
    - Address every failure pattern explicitly.
    - Preserve the JSON `plan` output contract.
""").strip()


class PromptGenerator:
    """走 Claude 生成与优化 system prompt。"""

    def __init__(self, client: ClaudeClient | None = None):
        self.client = client

    def generate(self, task: dict[str, Any]) -> str:
        """根据 task dict 生成 system prompt。"""
        if self.client is None:
            return self._fallback_template(task)

        user = self._format_task_for_claude(task)
        return self.client.call(
            system=META_PROMPT_GENERATE,
            user=user,
            max_tokens=1500,
            temperature=0.4,
            tag="prompt_generate",
        ).strip()

    def optimize(self, prev_prompt: str, feedback: str, task: dict[str, Any]) -> str:
        """根据失败反馈重写 system prompt。"""
        if self.client is None:
            return prev_prompt + f"\n\n# 注意事项（自动）\n{feedback}\n"

        user = textwrap.dedent(f"""
            ## Task spec
            {self._format_task_for_claude(task)}

            ## Previous system prompt
            ```
            {prev_prompt}
            ```

            ## Failures observed
            {feedback}

            Please rewrite the system prompt fully.
        """).strip()
        return self.client.call(
            system=META_PROMPT_OPTIMIZE,
            user=user,
            max_tokens=1800,
            temperature=0.4,
            tag="prompt_optimize",
        ).strip()

    @staticmethod
    def _format_task_for_claude(task: dict[str, Any]) -> str:
        """把 task dict 压缩成一段简洁描述，避免无谓 token。"""
        tools_lines = []
        for t in task.get("tools", []):
            params = ", ".join(f"{k}({v})" for k, v in t.get("parameters", {}).items())
            tools_lines.append(f"- {t['name']}: {t.get('description','')} [params: {params}]")
        scenarios = "\n".join(f"- {s}" for s in task.get("user_scenarios", []))
        return textwrap.dedent(f"""
            Agent name: {task.get('name','agent')}
            Business goal: {task.get('description','')}

            User scenarios:
            {scenarios or '- (none provided)'}

            Available tools:
            {chr(10).join(tools_lines) or '- (no tools)'}
        """).strip()

    @staticmethod
    def _fallback_template(task: dict[str, Any]) -> str:
        """dry-run 兜底（不调 Claude），用于无 token 联调。"""
        tool_names = [t["name"] for t in task.get("tools", [])]
        return textwrap.dedent(f"""
            你是一个任务执行智能体：{task.get('name','agent')}。
            目标：{task.get('description','')}
            可用工具：{', '.join(tool_names)}。
            必须以 JSON 输出：{{"plan":[{{"task":"<tool>","args":{{}},"reason":"..."}}]}}
            禁止使用列表外的工具。
        """).strip()


if __name__ == "__main__":
    # dry-run 自测
    task = {
        "name": "alarm_query_agent",
        "description": "查询告警",
        "tools": [{"name": "query_alarms", "description": "查询告警", "parameters": {"date": "YYYY-MM-DD"}}],
        "user_scenarios": ["用户问今天的告警"],
    }
    print(PromptGenerator()._fallback_template(task))
