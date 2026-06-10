# Agent-of-Agent 元智能体平台

> **核心流程**：你写一份 Markdown 任务规范 → Claude 生成 system prompt 与代码 →
> Qwen3-VL-4B-Instruct-FP8 跑测试 → 通过则注册到 FastAPI → 真实 SQL 业务库可用。

## 关键启动命令

```bash
# 1. 设置 IMDS 代理凭证
export ANTHROPIC_AUTH_TOKEN=sk-de843a0c85
export ANTHROPIC_BASE_URL=https://imds.ai/
export ANTHROPIC_MODEL=claude

# 2. 激活 conda 环境
conda activate agent
cd /mnt/data3/clip/LangGraph/agent/agent

# 3. 准备数据底座（首次必跑）
python data/seed.py

# 4. CLI 跑流水线（生成新 Agent）
bash autoctl.sh start templates/AGENT_SPEC_EXAMPLE.md
bash autoctl.sh status <job_id>
bash autoctl.sh logs   <job_id>
bash autoctl.sh list

# 5. 发布到 FastAPI
python publish.py <job_id>

# 6. 启动 Web 控制台（可选，更易用）
bash web/start_web.sh        # http://0.0.0.0:7860
```

## 文件地图

```
agent/agent/
├── RULES.md                  ★ 通用契约（接口/工具调用/审计/版本）
├── autoctl.sh                ★ 后台守护进程（start/status/logs/list/stop）
├── run_meta_agent.py         ★ 流水线主脚本
├── publish.py                ★ 发布到 FastAPI
├── registry.py               注册表读写
│
├── templates/
│   ├── AGENT_SPEC_TEMPLATE.md ★ 任务规范模板（必读）
│   └── AGENT_SPEC_EXAMPLE.md  告警查询填好示例
│
├── data/
│   ├── CHECKLIST.md           ★ 数据库准备清单
│   ├── schema.sql             SQLite DDL
│   ├── schema_mysql.sql       MySQL 8.0 DDL（平台对接）
│   ├── seed.py                生成模拟数据
│   └── README.md
│
├── meta_agent/
│   ├── llm_client.py          Anthropic SDK 封装（IMDS 代理 + 预算）
│   ├── prompt_generator.py    走 Claude 生成/优化 prompt
│   ├── code_generator.py      模板填充：注入 prompt + 真 SQLite 工具
│   ├── tool_impl.py           query_alarms / query_video / query_person 真实现
│   ├── executor.py            子进程隔离 + plan-based 判定（不再用 stdout）
│   ├── evaluator.py           三维加权：tool_accuracy + execution + case_pass
│   ├── feedback_analyzer.py   失败原因 → Claude 反馈摘要
│   └── spec_parser.py         markdown spec → task dict
│
├── web/
│   ├── app.py                 ★ Gradio 4 Tab 控制台
│   ├── job_manager.py         任务状态（独立 SQLite）
│   ├── start_web.sh
│   └── README.md
│
├── registry/
│   └── agent_registry.json    ★ 已发布 Agent 单一事实源
│
├── artifacts/
│   ├── <job_id>/              每次跑流水线的产出
│   │   ├── spec.md
│   │   ├── system_prompt.txt
│   │   ├── agent_code.py      ★ 生成的可运行 Agent
│   │   ├── metrics.json
│   │   ├── test_report.json
│   │   ├── claude_log.jsonl   每次 Claude 调用的 token 累计
│   │   └── REGISTER.json
│   └── published/<name>_v<ver>.py  发布后的归档
│
└── logs/jobs/<job_id>/run.log autoctl 启动产生的进程日志
```

## 数据流

```
spec.md  →  spec_parser  →  task dict
            │
            ▼
        PromptGenerator (Claude)  ───►  system_prompt.txt
            │
            ▼
        CodeGenerator (模板)      ───►  agent_code.py
            │                            │
            │                            ▼ 真连
            │                         SQLite (data/ksipms_dev.db)
            ▼
        Executor (子进程)
        - 调 agent.run(test_case.input)
        - Qwen3-VL-4B (port 8004) 决策
        - Plan + tool_results 落盘
            │
            ▼
        Evaluator → metrics.json
            │
            ├─ pass → REGISTER.json（passed_acceptance=true）→ publish 到 /agents/<name>/chat
            └─ fail → FeedbackAnalyzer (Claude) → PromptGenerator.optimize → 下一轮
```

## RULES.md 关键约束（节选）

- 每个 Agent 必须暴露 `def run(user_message: str, **ctx) -> dict`，返回固定结构
- `plan` 中工具名必须在 spec 白名单里，否则视为幻觉
- 每次工具调用必须写 `audit_log`（best-effort）
- Token 预算 `INPUT=50000 / OUTPUT=20000`，超额抛 BudgetExceeded
- 不允许在 stdout 里 print 关键决策——评估器只看 plan 字典

## 已知限制（下一阶段）

- 知识库 / RAG 未启用（spec §6 是占位）
- 真实 MCP 工具协议未接（仍用 SQLite 模拟）
- Multi-Agent 协同未实现（agent_registry 已留位）
- 公司平台 SSO/RBAC 未实现

## 文档索引

- `feasibility_analysis.md` — 早期可行性分析（参考）
- `SUMMARY.md` — MVP 阶段总结（参考）
- `RULES.md` — **当前生效的强制规则**
- `data/CHECKLIST.md` — 数据库准备清单
- `templates/AGENT_SPEC_TEMPLATE.md` — 任务描述模板
- `web/README.md` — Web 控制台用法
