# KSAgent - 安全生产场景 AI 智能体

> 基于 LangGraph + Qwen3-VL + MCP 的多模态智能体，用于 YOLO 告警复判、自然语言任务编排、知识库联动

## 核心能力

1. **多模态告警复判**：接收 YOLO 检测结果 + 原图，调用 Qwen3-VL-4B 进行二次确认（抽烟/安全帽/手机/口罩）
2. **自然语言操作**：文字/语音输入 → LLM 解析意图 → 调用 MCP 工具（查询告警、检索录像、查人员信息）
3. **知识库联动**：RAG 查询规章制度，结合图像识别结果给出违规条款
4. **Plan-Execute 编排**：复杂任务自动拆解为子任务，串行/并行执行

## 技术栈

| 组件 | 技术选型 | 说明 |
|---|---|---|
| **Web 框架** | FastAPI 0.115+ | 异步 API、WebSocket 流式输出 |
| **智能体编排** | LangGraph 0.2+ | 状态图、Plan-Execute 模式 |
| **LLM 后端** | Qwen3-VL-4B-Instruct (vLLM) | 本地部署 `http://127.0.0.1:8002/v1` |
| **工具协议** | MCP (Model Context Protocol) | 对接 ksipms 平台业务接口 |
| **知识库** | RAGFlow (可选) | 规章制度文档检索 |
| **数据库** | MySQL 8.0 + SQLAlchemy 2.0 | 会话历史、任务日志 |
| **语音** | Whisper / FunASR | 语音转文字（待接入） |

## 项目结构

```
agent/
├── README.md                    # 本文档
├── requirements.txt             # Python 依赖
├── config.yaml                  # 配置文件（模型 URL、数据库、MCP 端点）
├── main.py                      # FastAPI 入口
├── graph/
│   ├── __init__.py
│   ├── state.py                 # LangGraph 状态定义
│   ├── nodes.py                 # 图节点：planner / executor / vlm_judge / rag_query
│   ├── graph.py                 # 图构建：Plan-Execute 主流程
│   └── tools.py                 # 工具函数：MCP 调用封装
├── mcp/
│   ├── __init__.py
│   ├── client.py                # MCP 客户端（连接 ksipms 平台）
│   └── schemas.py               # MCP 请求/响应数据结构
├── models/
│   ├── __init__.py
│   ├── database.py              # SQLAlchemy 模型（会话、任务）
│   └── schemas.py               # Pydantic 请求/响应模型
├── utils/
│   ├── __init__.py
│   ├── vlm.py                   # Qwen3-VL 调用封装
│   └── rag.py                   # RAGFlow 接口（可选）
└── tests/
    ├── test_api.py              # FastAPI 端点测试
    ├── test_graph.py            # LangGraph 图执行测试
    └── test_vlm.py              # VLM 多模态推理测试
```

## API 设计

### 1. 文本对话（自然语言操作）

**请求**：
```bash
POST /api/v1/chat
Content-Type: application/json

{
  "session_id": "user123_20260601",
  "message": "今天发生了哪几种告警事件？",
  "stream": false
}
```

**响应**：
```json
{
  "session_id": "user123_20260601",
  "response": "今天共发生 3 类告警：\n1. 未戴安全帽 (5 次)\n2. 抽烟 (2 次)\n3. 接打电话 (1 次)",
  "tool_calls": [
    {"tool": "query_alarms", "args": {"date": "2026-06-01"}, "result": "..."}
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
yolo_result: {"class": "no_helmet", "confidence": 0.87, "bbox": [100, 200, 300, 400]}
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
  "reasoning": "图中人员未佩戴安全帽，但戴了口罩，未见抽烟或使用手机行为。",
  "confidence": 0.92,
  "elapsed_ms": 856
}
```

### 3. 知识库联动复判

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
    {"rule_id": "SOP-2024-03", "title": "禁烟区管理规定", "excerpt": "..."}
  ],
  "elapsed_ms": 1523
}
```

### 4. 流式输出（WebSocket）

```javascript
const ws = new WebSocket('ws://localhost:8000/api/v1/chat/stream');
ws.send(JSON.stringify({
  session_id: "user123",
  message: "帮我找出今天9点到9点10分摄像机A的录像"
}));

ws.onmessage = (event) => {
  const chunk = JSON.parse(event.data);
  // chunk.type: "thought" | "tool_call" | "response" | "done"
  console.log(chunk);
};
```

## LangGraph 图结构（Plan-Execute 模式）

```
┌─────────┐
│  START  │
└────┬────┘
     │
     v
┌─────────────┐
│  Planner    │  ← LLM 解析用户意图，生成任务列表
│  (LLM)      │     例如："查询告警" → ["call_mcp_query_alarms", "format_response"]
└────┬────────┘
     │
     v
┌─────────────┐
│  Executor   │  ← 逐个执行任务：
│  (Loop)     │     - 调用 MCP 工具
│             │     - 调用 VLM 复判
│             │     - 查询 RAG 知识库
└────┬────────┘
     │
     v
┌─────────────┐
│  Replan?    │  ← 检查是否需要重新规划（任务失败/结果不足）
└─┬─────────┬─┘
  │ Yes     │ No
  v         v
 (回 Planner)  ┌─────────────┐
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
    plan: List[Dict[str, Any]]  # [{"task": "query_alarms", "args": {...}, "status": "pending"}]
    current_task_idx: int
    tool_results: List[Dict[str, Any]]
    final_response: str
    error: str | None
```

## 配置文件（config.yaml）

```yaml
# LLM 配置
llm:
  base_url: "http://127.0.0.1:8002/v1"
  model: "Qwen3-VL-4B-Instruct"
  api_key: "EMPTY"
  temperature: 0.2
  max_tokens: 2048

# MCP 配置（ksipms 平台）
mcp:
  endpoint: "http://ksipms.internal:9000/mcp"
  timeout: 30
  tools:
    - name: "query_alarms"
      description: "查询告警记录"
    - name: "query_video"
      description: "检索录像片段"
    - name: "query_person"
      description: "查询人员信息"

# RAG 知识库（可选）
rag:
  enabled: false
  ragflow_url: "http://ragflow.internal:8080"
  dataset_id: "safety_regulations"

# 数据库
database:
  url: "mysql+aiomysql://ksagent:password@localhost:3306/ksagent"
  pool_size: 10

# FastAPI
server:
  host: "0.0.0.0"
  port: 8000
  reload: false
```

## 成功标准（验收条件）

### 阶段 1：最小可运行框架（本次交付）
- [ ] FastAPI 启动成功，`GET /health` 返回 200
- [ ] 文本对话接口能调通 vLLM，返回 LLM 生成的回复
- [ ] LangGraph 图能跑通一个简单的 Plan-Execute 流程（mock MCP 工具）
- [ ] 多模态接口能接收图片 + 文本，调用 Qwen3-VL 返回判断结果

### 阶段 2：MCP 集成
- [ ] MCP 客户端能连接 ksipms 平台，调用 `query_alarms` 工具
- [ ] Plan-Execute 图能根据用户意图自动选择并调用 MCP 工具
- [ ] 错误处理：MCP 调用失败时能 replan 或返回友好错误

### 阶段 3：知识库联动
- [ ] RAGFlow 接口能检索规章制度文档
- [ ] 多模态复判 + 知识库查询能串联执行，返回违规条款

### 阶段 4：生产优化
- [ ] 数据库持久化会话历史
- [ ] WebSocket 流式输出
- [ ] 语音输入接入（Whisper / FunASR）
- [ ] 性能优化：并发请求、缓存、超时控制

## 参考资料

**架构参考**：
- [FastAPI + LangGraph + MCP 生产级模板](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template)
- [Building Smart Web AI Agents with MCP, LangGraph & FastAPI](https://sgino209.medium.com/building-smart-web-ai-agents-with-mcp-langgraph-fastapi-da2734fe5256)
- [MCP 多服务器架构](https://github.com/junfanz1/MCP-MultiServer-Interoperable-Agent2Agent-LangGraph-AI-System)

**Plan-Execute 模式**：
- [LangChain Plan-and-Execute Agents](https://blog.langchain.com/planning-agents/)
- [LangGraph Plan-Execute Tutorial](https://github.com/langchain-ai/langgraph/discussions/571)
- [Agentic RAG with LangGraph](https://www.learnwithparam.com/blog/agentic-rag-langgraph-planning-rewriting-tool-use)

**LangGraph 官方文档**：
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

## 下一步

1. **立即执行**：搭建最小可运行框架（阶段 1）
2. **并行开发**：其他部门准备 MCP 服务端点、RAGFlow 知识库
3. **迭代集成**：按阶段 2 → 3 → 4 逐步完善功能
