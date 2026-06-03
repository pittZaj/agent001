"""Anthropic SDK 封装：Agent-of-Agent 元智能体唯一对外的 Claude 入口。

借鉴 /mnt/data3/clip/work-clothes/ConvNeXt-V2-wc/autoresearch_loop.py:15-116 的模式：
- 通过 IMDS 代理（base_url=https://imds.ai/）使用 ANTHROPIC_AUTH_TOKEN
- 默认模型 claude-sonnet-4-6（可由 ANTHROPIC_MODEL 覆盖为 claude-opus-4-7 等）
- 每次调用都把 token 用量回吐，便于 run_meta_agent 累计写入 claude_log.jsonl

预算控制：超过 TOKEN_BUDGET_INPUT / TOKEN_BUDGET_OUTPUT 抛 BudgetExceeded，
autoctl.sh 据此把 job 标记为 budget_exhausted。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from anthropic import Anthropic


class BudgetExceeded(RuntimeError):
    """Token 预算超额时抛出。"""


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: dict) -> None:
        self.input_tokens += int(other.get("input", 0) or 0)
        self.output_tokens += int(other.get("output", 0) or 0)

    def asdict(self) -> dict:
        return {"input": self.input_tokens, "output": self.output_tokens}


@dataclass
class ClaudeClient:
    """对话级 Claude 客户端：累计 token，落盘 jsonl，校验预算。"""

    log_path: Optional[Path] = None
    model: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    base_url: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_BASE_URL", "https://imds.ai/"))
    budget_input: int = field(default_factory=lambda: int(os.environ.get("TOKEN_BUDGET_INPUT", "50000")))
    budget_output: int = field(default_factory=lambda: int(os.environ.get("TOKEN_BUDGET_OUTPUT", "20000")))
    usage: TokenUsage = field(default_factory=TokenUsage)
    _client: Optional[Anthropic] = field(default=None, init=False)

    def __post_init__(self):
        token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "缺少 ANTHROPIC_AUTH_TOKEN 环境变量。\n"
                "请先 export ANTHROPIC_AUTH_TOKEN=<your_token>"
            )
        self._client = Anthropic(api_key=token, base_url=self.base_url)

    def call(self, system: str, user: str, *, max_tokens: int = 4096,
             temperature: float = 0.2, tag: str = "") -> str:
        """同步调用 Claude，返回纯文本。

        Args:
            system: system prompt
            user: 单轮 user message
            max_tokens: 输出上限
            temperature: 0.2 默认（确定性偏高）
            tag: 该次调用的语义标签（写入 claude_log.jsonl 便于后期分析）
        """
        self._check_budget(estimate_input=len(user) // 3 + len(system) // 3, estimate_output=max_tokens)
        t0 = time.time()
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        dt = time.time() - t0
        text = msg.content[0].text if msg.content else ""
        usage = {"input": getattr(msg.usage, "input_tokens", 0),
                 "output": getattr(msg.usage, "output_tokens", 0)}
        self.usage.add(usage)
        self._log(tag=tag, system=system, user=user, output=text, usage=usage, dt=dt)
        return text

    def _check_budget(self, estimate_input: int, estimate_output: int) -> None:
        # 仅做硬上限：累计 + 估计 < 预算
        if self.usage.input_tokens + estimate_input > self.budget_input:
            raise BudgetExceeded(
                f"input token 预算超限: used={self.usage.input_tokens} + est={estimate_input} > {self.budget_input}"
            )
        if self.usage.output_tokens + estimate_output > self.budget_output:
            raise BudgetExceeded(
                f"output token 预算超限: used={self.usage.output_tokens} + est={estimate_output} > {self.budget_output}"
            )

    def _log(self, *, tag: str, system: str, user: str, output: str, usage: dict, dt: float) -> None:
        if not self.log_path:
            return
        record = {
            "ts": int(time.time()),
            "tag": tag,
            "model": self.model,
            "duration_sec": round(dt, 2),
            "usage": usage,
            "cumulative": self.usage.asdict(),
            "system_preview": system[:200],
            "user_preview": user[:400],
            "output_preview": output[:400],
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # 烟测：依赖 ANTHROPIC_AUTH_TOKEN
    cli = ClaudeClient(log_path=Path("/tmp/claude_smoke.jsonl"))
    out = cli.call(
        system="You answer in one short sentence.",
        user="What is 17 * 23?",
        max_tokens=64,
        tag="smoke",
    )
    print("output:", out)
    print("usage :", cli.usage.asdict())
