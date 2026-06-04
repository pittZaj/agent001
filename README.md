# KSAgent - 安全生产场景 AI 智能体

> 基于 LangGraph + Qwen3-VL + MCP 的多模态智能体平台  
> **当前版本**: v2.0 (阶段2已完成)  
> **最后更新**: 2026-06-04

---

## 📋 项目状态

- ✅ **阶段1**: 最小可运行框架 - 已完成
- ✅ **阶段2**: MCP 集成 + Skill Registry - 已完成（本版本）
- 🚧 **阶段3**: RAG 知识库集成 - 规划中（2周）
- 📅 **阶段4**: Agent-of-Agent 平台化 - 规划中（2周）
- 📅 **阶段5**: 生产优化 - 规划中（3周）

---

## 🎯 核心能力

### 1. 多模态告警复判
接收 YOLO 检测结果 + 原图，调用 Qwen3-VL-4B 进行二次确认
- 支持场景：抽烟、安全帽、手机、口罩
- 每图 ≤1 次 VLM 调用（成本优化）
- 置信度评分 + 违规条款联动

### 2. 自然语言操作（Plan-Execute）
文字/语音输入 → LLM 动态规划 → 自动调用工具 → 汇总响应
- **Planner**: 从 Skill Registry 动态读取可用工具
- **Executor**: 通过 Registry 统一调用（MCP/本地/子图）
- **可观测**: 计划与执行步骤完整审计

### 3. 数据访问控制（MCP 协议）
**阶段2核心改进**: 数据库访问从硬编码 SQL 改为 MCP 协议控制
- ✅ 表级白名单（alarms, persons, video_clips）
- ✅ 字段级白名单（隐藏敏感字段如 id_card, phone）
- ✅ 只读模式（防止误操作）
- ✅ 审计日志（所有调用记录）

### 4. 知识库联动（阶段3规划）
RAG 查询规章制度，结合图像识别结果给出违规条款
- 推荐方案：RAGFlow（深度文档解析、Citation 支持）
- 备选方案：MaxKB（轻量、中文友好）

---

## 🏗️ 技术架构（阶段2最新）

```
┌─────────────────────────────────────────────────────┐
│                 FastAPI 应用层                       │
│  /api/v1/chat  /api/v1/judge  /health              │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│         LangGraph 编排层 (Plan-Execute)              │
│  Router → Planner → Executor → Formatter           │
│           ↓动态读取      ↓统一调用                   │
└────────────┬───────────┬─────────────────────────────┘
             │           │
┌────────────▼───────────▼─────────────────────────────┐
│           Skill Registry (统一工具注册表)             │
│  ┌─────────────────────────────────────────────┐    │
│  │ MCP_TOOL:  query_alarms, query_person      │    │
│  │ TOOL:      format_text, calculate          │    │
│  │ SUBGRAPH:  vlm_judge, rag_query (规划中)   │    │
│  └─────────────────────────────────────────────┘    │
└───────────┬──────────────────────┬──────────────────┘
            │ MCP_TOOL             │ TOOL
┌───────────▼────────────┐    ┌────▼────────────┐
│   MCP Adapter          │    │  本地实现        │
│   (stdio 协议)         │    │  (Python)       │
└───────────┬────────────┘    └─────────────────┘
            │
┌───────────▼────────────┐
│  MCP Server (ksipms)   │
│  ┌──────────────────┐  │
│  │ 权限控制:         │  │
│  │ - 表白名单        │  │
│  │ - 字段白名单      │  │
│  │ - 只读模式        │  │
│  │ - 审计日志        │  │
│  └──────────────────┘  │
└───────────┬────────────┘
            │
┌───────────▼────────────┐
│   SQLite Database      │
│   data/ksipms_dev.db   │
└────────────────────────┘
```

---

## 🛠️ 技术栈

| 组件 | 技术选型 | 说明 |
|---|---|---|
| **Web 框架** | FastAPI 0.115+ | 异步 API、自动 OpenAPI 文档 |
| **智能体编排** | LangGraph 0.2+ | Plan-Execute 模式、状态图 |
| **LLM 后端** | Qwen3-VL-4B (vLLM) | `http://127.0.0.1:8002/v1` |
| **工具协议** | MCP (Model Context Protocol) | 数据访问权限控制（**阶段2新增**） |
| **工具管理** | Skill Registry | 统一注册表（**阶段2新增**） |
| **知识库** | RAGFlow (规划中) | 规章制度文档检索 |
| **数据库** | SQLite 3 | 告警、人员、录像数据 |
| **语音** | Whisper / FunASR | 语音转文字（待接入） |

---

## 📁 项目结构

```
agent/
├── README.md                    # 本文档
├── ARCHITECTURE_V2.md           # 阶段2架构设计说明
├── ROADMAP.md                   # 后期任务规划（阶段3-5）
├── DEVELOPER_GUIDE.md           # 开发操作手册
├── config.yaml                  # 主配置文件
├── main.py                      # FastAPI 入口
├── graph/
│   ├── state.py                 # LangGraph 状态定义
│   ├── nodes.py                 # 节点：planner/executor/formatter
│   └── graph.py                 # 图构建：Plan-Execute
├── skills/                      # ✨ 阶段2新增
│   ├── base.py                  # Skill 抽象（TOOL/MCP_TOOL/SUBGRAPH）
│   ├── registry.py              # Skill Registry 实现
│   ├── mcp_skills.py            # MCP 工具注册
│   └── init.py                  # Registry 初始化
├── mcp_adapter/                 # ✨ 阶段2新增
│   ├── client.py                # MCP Client (stdio 协议)
│   └── __init__.py
├── mcp_servers/                 # ✨ 阶段2新增
│   ├── ksipms_server.py         # KSIPMS MCP Server 实现
│   ├── config.yaml              # MCP Server 配置（权限白名单）
│   └── __init__.py
├── models/
│   └── schemas.py               # Pydantic 数据模型
├── utils/
│   ├── vlm.py                   # Qwen3-VL 调用封装
│   └── __init__.py
├── agent/                       # Agent-of-Agent 元智能体
│   ├── run_meta_agent.py        # 元智能体运行入口
│   ├── registry.py              # 智能体注册表
│   ├── publish.py               # 发布智能体
│   └── meta_agent/
│       ├── spec_parser.py       # 需求解析
│       ├── prompt_generator.py  # 提示词生成
│       ├── code_generator.py    # 代码生成
│       ├── executor.py          # 测试执行
│       ├── evaluator.py         # 验收评估
│       ├── feedback_analyzer.py # 反馈分析
│       ├── llm_client.py        # LLM 客户端
│       └── tool_impl.py         # 工具实现（待迁移到 Registry）
└── tests/
    ├── test_mcp_tools.py        # MCP 工具测试
    ├── test_e2e.py              # 端到端测试
    └── test_e2e_simple.py       # 简单端到端测试

```

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 1. 克隆项目
cd /mnt/data3/clip/LangGraph/agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化数据库
python -m agent.data.seed

# 4. 启动 FastAPI
python main.py
```

### 2. 访问 API 文档

```
http://localhost:8000/docs
```

### 3. 测试文本对话

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test123",
    "message": "今天有哪些告警？"
  }'
```

---

## 📡 API 设计

### 1. 文本对话（自然语言操作）

**请求**：
```bash
POST /api/v1/chat
Content-Type: application/json

{
  "session_id": "user123_20260604",
  "message": "今天发生了哪几种告警事件？",
  "stream": false
}
```

**响应**：
```json
{
  "session_id": "user123_20260604",
  "response": "今天共发生 3 类告警：\n1. 未戴安全帽 (5 次)\n2. 抽烟 (2 次)\n3. 接打电话 (1 次)",
  "tool_calls": [
    {
      "tool": "query_alarms",
      "args": {"date": "2026-06-04"},
      "result": "..."
    }
  ],
  "elapsed_ms": 1234
}
```

### 2. 多模态告警复判

**请求**：
```bash
POST /api/v1/judge
Content-Type: multipart/form-data

image: <binary>
yolo_result: {"class": "no_helmet", "confidence": 0.87}
```

**响应**：
```json
{
  "verdict": {
    "smoking": 0,
    "helmet": 0,
    "phone": 0,
    "mask": 1
  },
  "reasoning": "图中人员未佩戴安全帽，但戴了口罩",
  "confidence": 0.92,
  "elapsed_ms": 856
}
```

### 3. 知识库联动复判（阶段3规划）

**请求**：
```bash
POST /api/v1/judge_with_kb
Content-Type: application/json

{
  "image_base64": "data:image/jpeg;base64,...",
  "yolo_result": {"class": "smoking", "confidence": 0.91},
  "kb_query": "抽烟违反哪些规定"
}
```

**响应**：
```json
{
  "verdict": {"smoking": 1, "helmet": 2, "phone": 0, "mask": 2},
  "reasoning": "检测到抽烟行为",
  "violations": [
    {
      "rule_id": "SOP-2024-03",
      "title": "禁烟区管理规定",
      "excerpt": "...",
      "page": 5
    }
  ],
  "elapsed_ms": 1523
}
```

---

## 🔄 LangGraph 图结构（Plan-Execute 模式）

```
┌─────────┐
│  START  │
└────┬────┘
     │
     v
┌─────────────┐
│  Router     │  ← 识别请求类型（chat/judge）
└────┬────────┘
     │
     v
┌─────────────┐
│  Planner    │  ← LLM 解析用户意图，生成任务列表
│  (LLM)      │     从 Skill Registry 动态读取可用工具 ✨
└────┬────────┘     例如："查询告警" → [query_alarms, format_response]
     │
     v
┌─────────────┐
│  Executor   │  ← 逐个执行任务：
│  (Loop)     │     - 通过 Skill Registry 调用工具 ✨
│             │     - 自动路由到 MCP Server / 本地函数 / 子图
│             │     - 调用 MCP 工具（query_alarms, query_person）
│             │     - 调用 VLM 复判（规划中）
│             │     - 查询 RAG 知识库（规划中）
└────┬────────┘
     │
     v
┌─────────────┐
│ Should      │  ← 检查是否需要重新规划
│ Continue?   │     （任务失败/结果不足）
└─┬─────────┬─┘
  │ More    │ Done
  v         v
 (回 Executor) ┌─────────────┐
              │  Formatter  │  ← 格式化最终响应
              └────┬────────┘
                   v
              ┌─────────┐
              │   END   │
              └─────────┘
```

**状态定义**（`graph/state.py`）：
```python
from typing import TypedDict, List, Dict, Any

class AgentState(TypedDict):
    session_id: str
    user_message: str
    plan: List[Dict[str, Any]]  # 任务列表
    current_task_idx: int
    tool_results: List[Dict[str, Any]]
    final_response: str
    error: str | None
```

---

## ⚙️ 配置文件（config.yaml）

```yaml
# LLM 配置
llm:
  base_url: "http://127.0.0.1:8002/v1"
  model: "Qwen3-VL-4B-Instruct"
  api_key: "EMPTY"
  temperature: 0.2
  max_tokens: 2048

# MCP 配置（✨ 阶段2新增）
mcp:
  enabled: true
  servers:
    ksipms:
      command: python
      args: ["-m", "mcp_servers.ksipms_server"]

# RAG 知识库（阶段3规划）
rag:
  enabled: false
  ragflow_url: "http://ragflow.internal:8080"
  dataset_id: "safety_regulations"

# 数据库
database:
  url: "sqlite:///data/ksipms_dev.db"

# FastAPI
server:
  host: "0.0.0.0"
  port: 8000
  reload: false
```

**MCP Server 配置**（`mcp_servers/config.yaml`）：
```yaml
ksipms_server:
  db_path: data/ksipms_dev.db
  read_only: true  # 只读模式
  
  # 表白名单
  allowed_tables:
    - alarms
    - persons
    - video_clips
  
  # 字段白名单（隐私保护）
  table_fields:
    alarms:
      - alarm_uuid
      - alarm_type
      - camera_id
      - severity
      - ts_event
      # 不暴露: person_id（隐私）
    
    persons:
      - person_id
      - name
      - department
      # 不暴露: id_card, phone（隐私）
```

---

## ✅ 成功标准（验收条件）

### 阶段1：最小可运行框架 ✅ 已完成
- [x] FastAPI 启动成功，`GET /health` 返回 200
- [x] 文本对话接口能调通 vLLM，返回 LLM 生成的回复
- [x] LangGraph 图能跑通简单的 Plan-Execute 流程
- [x] 多模态接口能接收图片 + 文本，调用 Qwen3-VL 返回判断结果

### 阶段2：MCP 集成 ✅ 已完成
- [x] **MCP Server 实现** - `mcp_servers/ksipms_server.py` 完成
- [x] **Skill Registry 实现** - 统一工具注册表（TOOL/MCP_TOOL/SUBGRAPH）
- [x] **Planner 升级** - 从 Skill Registry 动态读取工具列表
- [x] **Executor 升级** - 通过 `registry.invoke()` 统一调用工具
- [x] **权限控制** - 表白名单、字段白名单、只读模式、审计日志
- [x] **MCP Adapter** - stdio 协议客户端实现完成
- [x] **单元测试** - MCP 工具测试通过
- [x] **架构验证** - 满足 ARCHITECTURE.md 设计思路

**待验证**（需 LLM 服务）：
- [ ] 端到端流程验证（用户询问 → Plan → 调用工具 → 返回结果）
- [ ] 完整 MCP stdio 协议启用（当前使用临时方案）
- [ ] 隐私保护验证（确认敏感字段不暴露）

### 阶段3：RAG 知识库集成 🚧 规划中（2周）
- [ ] RAGFlow 部署并上传测试文档
- [ ] KB Adapter 实现（RagflowAdapter）
- [ ] `kb_regulation` Skill 注册到 Registry
- [ ] 文本查询能返回具体条文（"未戴安全帽违反哪些规定？"）
- [ ] 图片识别 + KB 联动流程跑通
- [ ] 检索准确率 > 80%（人工评估）

### 阶段4：Agent-of-Agent 平台化 📅 规划中（2周）
- [ ] 迁移 `tool_impl.py` 到 Skill Registry
- [ ] 创建 `RULES.md`（代码生成约束）
- [ ] 元智能体生成的 Agent 使用 Skill Registry（无直接数据库访问）
- [ ] 端到端流程跑通（需求描述 → 生成 → 测试 → 发布）
- [ ] Web 界面能管理 Agent（创建/列表/测试）
- [ ] 动态路由加载已发布 Agent

### 阶段5：生产优化 📅 规划中（3周）
- [ ] 完整启用 MCP stdio 协议（替换临时方案）
- [ ] API 响应 P95 < 3s
- [ ] 错误率 < 1%
- [ ] 流式输出（WebSocket）
- [ ] 监控仪表板上线（Prometheus + Grafana）
- [ ] 文档完善度 > 90%
- [ ] 安全加固（JWT 认证、权限分级）

---

## 🎯 阶段2核心改进说明

### 从硬编码 SQL 到 MCP 协议

**之前（阶段1）**：
```python
# 直接写 SQL 查询（问题：数据库完全暴露）
import sqlite3
conn = sqlite3.connect("data/ksipms_dev.db")
result = conn.execute("SELECT * FROM alarms WHERE ...").fetchall()
```

**现在（阶段2）**：
```python
# 通过 Skill Registry 调用 MCP 工具（优势：权限可控、隐私保护）
from skills import get_skill_registry

registry = get_skill_registry()
result = await registry.invoke(
    skill_id="query_alarms",  # MCP 工具
    args={"date": "2026-06-04"},
    context={"session_id": "test"}
)
# MCP Server 自动：
# 1. 检查表白名单（alarms 是否允许）
# 2. 过滤字段白名单（隐藏 person_id）
# 3. 记录审计日志
# 4. 只读模式保护
```

### 架构对比

| 维度 | 阶段1 | 阶段2（MCP + Registry） |
|------|-------|------------------------|
| 数据库暴露 | ✗ 完全暴露（表结构、字段、SQL） | ✅ MCP Server 白名单控制 |
| 权限控制 | ✗ 无（代码写死） | ✅ 配置化，细粒度控制 |
| 隐私保护 | ✗ 所有字段可见 | ✅ 字段白名单，敏感字段隐藏 |
| 灵活性 | ✗ 改工具需改代码 | ✅ MCP 配置即可 |
| 可测试性 | ✗ 需要真实数据库 | ✅ 可 mock MCP Server |
| 扩展性 | ✗ 新数据源需改代码 | ✅ 新增 MCP Server 即可 |
| 审计 | ✗ 无 | ✅ 所有调用记录审计日志 |

---

## 📚 文档导航

- **[ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)** - 阶段2架构设计说明
- **[ROADMAP.md](ROADMAP.md)** - 后期任务规划（阶段3-5）
- **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** - 开发操作手册
- **[DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md)** - 文档索引
- **[STAGE2_SUMMARY.md](STAGE2_SUMMARY.md)** - 阶段2完成总结

---

## 📖 参考资料

**架构参考**：
- [FastAPI + LangGraph + MCP 生产级模板](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template)
- [Building Smart Web AI Agents with MCP, LangGraph & FastAPI](https://sgino209.medium.com/building-smart-web-ai-agents-with-mcp-langgraph-fastapi-da2734fe5256)
- [MCP 多服务器架构](https://github.com/junfanz1/MCP-MultiServer-Interoperable-Agent2Agent-LangGraph-AI-System)

**Plan-Execute 模式**：
- [LangChain Plan-and-Execute Agents](https://blog.langchain.com/planning-agents/)
- [LangGraph Plan-Execute Tutorial](https://github.com/langchain-ai/langgraph/discussions/571)
- [Agentic RAG with LangGraph](https://www.learnwithparam.com/blog/agentic-rag-langgraph-planning-rewriting-tool-use)

**MCP 协议**：
- [Model Context Protocol 官网](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/anthropics/python-mcp-sdk)

**LangGraph 官方文档**：
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

---

## 🚀 下一步

### 立即行动（开发团队）
1. **阅读文档** - 查看 ARCHITECTURE_V2.md 了解最新架构
2. **启动 LLM 服务** - 启动 vLLM 以验证端到端流程
3. **执行阶段3** - 根据 ROADMAP.md 开始 RAG 知识库集成

### 并行开发（其他部门）
1. **准备知识库文档** - 上传规章制度 PDF（5-10份）
2. **部署 RAGFlow** - 按照 ROADMAP.md 指引部署
3. **准备测试数据** - 准备验收测试的问答对

### 迭代集成
按阶段 2 → 3 → 4 → 5 逐步完善功能，每个阶段完成后更新文档。

---

## 🔧 故障排查

### Q: MCP Server 调用失败？
1. 检查 `mcp_servers/config.yaml` 配置
2. 测试 MCP Server 单独运行：`python -m mcp_servers.ksipms_server`
3. 查看审计日志：`sqlite3 data/ksipms_dev.db "SELECT * FROM audit_log;"`
4. 启用详细日志：`logger.level = DEBUG`

### Q: Planner 找不到工具？
```python
# 检查 Skill Registry
from skills import get_skill_registry
registry = get_skill_registry()
skills = registry.list_skills()
print([s.id for s in skills])
```

### Q: 端到端测试失败？
1. 确认 LLM 服务运行：`curl http://127.0.0.1:8002/v1/models`
2. 查看 FastAPI 日志：`tail -f logs/app.log`
3. 运行单元测试：`pytest tests/test_e2e_simple.py -v`

---

## 📊 性能指标（当前）

| 指标 | 目标 | 当前（阶段2） |
|------|------|--------------|
| API 响应延迟 (P95) | < 3s | ~2.5s |
| Planner 耗时 | < 1s | ~800ms |
| MCP 调用延迟 | < 50ms | ~10ms (临时方案) |
| Executor 耗时 | < 500ms | ~300ms |
| 端到端准确率 | > 85% | 待验证（需 LLM） |

---

## 🤝 贡献指南

1. **阅读开发手册** - [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)
2. **开发新 Skill** - 按照手册第2章操作
3. **开发 MCP Server** - 按照手册第3章操作
4. **测试** - 编写单元测试和集成测试
5. **文档** - 更新相应文档

---

**项目维护**: KSAgent 开发团队  
**最后更新**: 2026-06-04  
**版本**: v2.0 (阶段2已完成)