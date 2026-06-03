# Agent-of-Agent 通用规则（"宪法"）

> 所有由元智能体生成的子 Agent、所有手写的 Agent、以及所有评估/调度/上线代码
> **必须**遵守本文件。后期扩展 RAG / 多 Agent 协同时，本文件规约不得违反。

## 0. 适用范围

- `/mnt/data3/clip/LangGraph/agent/agent/` 项目下的所有代码
- 由本项目生成并发布到 `/agents/<name>/chat` 的所有生产 Agent

## 1. Agent 接口契约

每个生产级 Agent 模块必须暴露：

```python
def run(user_message: str, **ctx) -> dict:
    """
    Args:
        user_message: 用户输入；保留字符串 "__healthcheck__" 用于健康检查。
        **ctx: 调用方上下文（trace_id, user_id, ...），允许忽略未知键。

    Returns:
        固定结构：
        {
            "response": str,              # 给用户看的最终自然语言回复
            "plan": [                     # 规划阶段产出的工具调用计划
                {"task": str, "args": dict, "reason": str}
            ],
            "tool_results": [             # 实际执行结果（与 plan 一一对应）
                {"task": str, "args": dict, "result": dict | None, "error": str | None}
            ],
            "error": str | None,          # 整体错误（None 表示成功）
            "trace_id": str               # 追溯 ID，进 audit_log
        }
    """
```

健康检查约定：

```python
run("__healthcheck__")  # 必须返回 {"response": "ok", "plan": [], "tool_results": [], "error": None, "trace_id": "..."}
```

## 2. 工具调用契约

`plan[i]` 必须严格符合：

```python
{"task": "<tool_name>", "args": {...}, "reason": "<为什么需要这一步>"}
```

- `task` 必须出现在该 Agent spec 的 `## 4. 可用工具` 表格中；否则视为幻觉。
- `args` 的键集合必须是工具参数的子集；多余键视为错误。
- 评估器以 **plan 字典**为唯一判定依据，**禁止**用 stdout 字符串包含来判断工具调用准确率。

## 3. 错误处理

- 工具内部异常必须被捕获并写入对应 `tool_results[i].error`，**不得**让 LangGraph 节点 `raise`。
- LLM 解析失败（JSON 格式错误等）写到顶层 `error`，并降级返回最佳摘要。
- 任何路径下都必须返回符合 §1 结构的 dict，**不得**返回 `None` 或抛出未捕获异常。

## 4. 审计契约

每次工具调用后，必须向 `audit_log` 表写一行：

```sql
INSERT INTO audit_log (alarm_id, action, operator_id, payload, ts)
VALUES (?, 'tool_call', 'agent:<name>', '<json with trace_id, tool, args_summary>', <epoch_seconds>);
```

- `payload` 是 JSON 文本，必须含 `trace_id`、`tool`、`args_digest`（args 的 sha1 前 8 位即可）
- 写失败不应影响主流程（单独 try/except，记录到 stderr）

## 5. 多 Agent 协同（前向兼容）

- 所有跨 Agent 调用必须通过 `tools_registry`（未来实现），不得直接 `import` 别的 Agent 模块。
- Agent 不得在 `run()` 内启动子进程或访问网络（除调用注册过的工具外）。
- 如未来需要 Agent 之间通信，统一约定通过共享 SQLite/MQ，不通过函数直调。

## 6. 元智能体（Meta-Agent）规则

- 元智能体使用 **Anthropic SDK + IMDS 代理**（`ANTHROPIC_BASE_URL=https://imds.ai/`，
  默认模型 `claude-sonnet-4-6`）。token 必须通过 `claude_log.jsonl` 累计落盘。
- 元智能体每次调用 Claude 必须传 spec 摘要 + 当前上下文；禁止把整个 SQLite 数据塞 prompt。
- 单 job 的 token 预算上限默认 `INPUT=50000 / OUTPUT=20000`，超出立刻终止。

## 7. 生产 Agent 模块路径与版本

- 发布后的 Agent 落到 `artifacts/published/<name>_v<major>_<minor>_<patch>.py`
- 注册表 `registry/agent_registry.json` 是**唯一事实源**；FastAPI 启动时只读它。
- 同名 Agent 升级版本号即可，旧版本保留 30 天再清理。

## 8. 数据访问规则

- 读 SQLite 必须使用 `read_only` URI（`file:...?mode=ro`）。
- 写库（仅 audit_log）必须用预编译参数化（防 SQL 注入），禁止字符串拼接 SQL。
- 不得跨 Agent 修改对方的业务表。

## 9. 时间戳

- 全部使用 UTC epoch 秒（`INTEGER`），展示层做时区转换。
- 用户输入"今天"等相对时间，Agent 应把当前 UTC 日期换成 `YYYY-MM-DD` 后再调工具。

## 10. 强制 Lint 项（评估器会自动检查）

- 不得 `print(plan)` 或 `print(json.dumps(...))` 然后让上层用字符串判工具
- 不得在 Agent 代码内硬编码模拟数据（mock 必须通过工具的 dry_run 参数）
- 不得在工具实现里 `raise` 而不被外层 try/except 包裹

---

**违反本文件任何一条的代码不得发布到 `artifacts/published/`。**
