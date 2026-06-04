# KSAgent 应用技术架构

> 面向工地/园区等安全生产场景的 AI 智能体：**图片识别 API** + 多模态大模型识别判断、文本任务编排、语音交互、规章制度知识库联动。

---

## 1. 总体架构

采用 **前后端分离 + 模块化单体（Modular Monolith）** 起步，后续可按模块拆分为微服务。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         客户端（Vue 3 SPA）                              │
│  图片/关键词上传 │ 文本对话 │ 语音录入 │ 结果展示（违规项+规章条文）        │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ HTTPS / REST / WebSocket(可选)
┌───────────────────────────────────▼─────────────────────────────────────┐
│                    API 网关层（Nginx / Traefik）                          │
│              鉴权、限流、TLS、静态资源、反向代理                           │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│              应用服务层（Python · FastAPI）                                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │     LangGraph 统一编排层（Plan-Execute + Skill Registry）         │   │
│  │  路由 → 规划(Plan) → 执行(Execute Skill/子图) → 复规划 → 汇总      │   │
│  └───────────────┬──────────────────────────────┬───────────────────┘   │
│                  │                              │                       │
│     ┌────────────▼────────────┐    ┌────────────▼────────────┐          │
│     │ 视觉 Skill 子图          │    │ 数据/知识 Skill 子图   │          │
│     │ 图片识别 API 编排        │    │ 告警/人员/KB/ASR      │          │
│     │ VLM判断→规章检索         │    │                       │          │
│     └─────────────────────────┘    └─────────────────────────┘          │
│                  共享：配置 / 日志 / 鉴权 / 对象存储 / thread 状态       │
└───────┬─────────────────┬─────────────────┬─────────────────┬──────────────┘
        │                 │                 │                 │
        ▼                 ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│ 多模态 VLM   │  │ 文本 LLM     │  │ ASR 引擎     │  │ 开源知识库平台         │
│ 图片识别判断 │  │ (Planner)    │  │ FunASR /     │  │ RAGFlow / Dify /      │
│ 每图 ≤1 次   │  │              │  │ Whisper      │  │ MaxKB 等              │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘
        │                 │                                    │
        └─────────────────┴────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌──────────────┐            ┌──────────────┐            ┌──────────────┐
│ PostgreSQL   │            │ Redis        │            │ MinIO / OSS  │
│ 告警/人员/   │            │ 缓存/会话/   │            │ 图片/音频    │
│ 识别记录     │            │ 任务队列     │            │ 原始文件     │
└──────────────┘            └──────────────┘            └──────────────┘
```

---

## 2. 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 前端框架 | **Vue 3** + **TypeScript** | 组合式 API，类型安全 |
| 前端构建 | **Vite** | 快速 HMR，生产构建 |
| UI 组件库 | **Element Plus** | 表单、上传、表格、对话 UI |
| 状态管理 | **Pinia** | 会话、用户信息、任务状态 |
| HTTP 客户端 | **Axios** | 统一拦截器、Token、错误处理 |
| 后端框架 | **FastAPI** | 异步、自动 OpenAPI、类型校验 |
| 运行时 | **Python 3.11+** | 与 AI 生态兼容好 |
| ASGI 服务器 | **Uvicorn** + **Gunicorn**（生产） | 多 worker 部署 |
| 数据校验 | **Pydantic v2** | 请求/响应模型 |
| ORM | **SQLAlchemy 2.0** + **Alembic** | 迁移、告警库查询 |
| 主数据库 | **PostgreSQL 15+** | 告警、人员、识别审计 |
| 缓存/队列 | **Redis 7** | 会话、限流、Celery Broker（可选） |
| 对象存储 | **MinIO**（私有化）/ 云 OSS | 图片、录音文件 |
| 异步任务 | **Celery** 或 **ARQ**（可选） | 大批量识别、报表 |
| 容器化 | **Docker** + **Docker Compose** | 本地与生产一致 |
| 反向代理 | **Nginx** | 静态资源、API 代理、HTTPS |

---

## 3. 功能模块与技术选型

### 3.1 Web 接口层

**职责**：对外提供 REST API（可选 WebSocket 推送长任务进度）。

| 能力 | 选型 | 理由 |
|------|------|------|
| API 框架 | FastAPI | 原生 async、OpenAPI 文档、与 Pydantic 一体 |
| 鉴权 | **JWT**（`python-jose`）+ API Key（系统对接） | 前后端分离标准方案 |
| 文件上传 | `python-multipart` + 大小/类型校验 | 图片、音频 multipart |
| API 文档 | Swagger UI（FastAPI 内置） | 联调、对接第三方 |
| 跨域 | `CORSMiddleware` | 开发期 Vue dev server |

**核心接口（建议）**：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/vision/analyze` | 图片 + 可选关键词 → 违规结论 + 规章引用 |
| POST | `/api/v1/agent/query` | 文本自然语言 → 编排执行 → 结构化结果 |
| POST | `/api/v1/speech/transcribe` | 音频 → 文本 → 可走 agent 流程 |
| POST | `/api/v1/kb/search` | 直接检索知识库（调试/管理） |
| GET | `/api/v1/alerts` | 告警列表（编排内部也会用） |

---

### 3.2 图片识别 API

**职责**：对外提供统一的 **图片识别判断接口**；初判/检测链路在 KSAgent 架构外实现，本系统不描述、不承载。KSAgent 接收图片与关键词，经编排调用 **VLM 完成识别判断**（每图最多 1 次），再联动知识库返回违规结论与规章引用（详见 **§3.6**）。

#### 接口契约

**`POST /api/v1/vision/analyze`**

请求（`multipart/form-data` 或 JSON + 图片 URL）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `image` | file / string | 是 | 图片文件或 MinIO/HTTP URL |
| `keywords` | string[] | 否 | 关注场景，如 `["未戴安全帽","抽烟"]`；缺省则按配置全场景 |
| `strict` | boolean | 否 | 是否启用更严格的 VLM 判断 Prompt |

响应（`VisionAnalyzeResponse`）：

```json
{
  "request_id": "uuid",
  "violations": [
    {
      "type": "no_helmet",
      "confidence": 0.92,
      "description": "画面左侧工人未佩戴安全帽",
      "bbox": [120, 80, 200, 360]
    }
  ],
  "regulations": [
    {
      "title": "施工现场安全管理规定 第×条",
      "excerpt": "...进入施工现场必须正确佩戴安全帽...",
      "source": "kb://doc-id#chunk-3"
    }
  ],
  "summary": "检测到 1 处未戴安全帽违规，适用上述规章。",
  "vlm_invoked": true
}
```

#### 编排内实现（不对外的实现细节）

| 组件 | 选型 | 说明 |
|------|------|------|
| VLM | 通义千问-VL / GLM-4V / Qwen2-VL + vLLM | **唯一视觉推理**，每图 ≤1 次 |
| Prompt | `config/vision_analyze_prompt.yaml` + `vision_scenes.yaml` | 关键词映射检测项 |
| 结构化输出 | JSON Schema + Instructor | 强制 `violations[]` |
| 存储 | MinIO 存原图；PostgreSQL `vision_records` 存请求与 VLM 结果 | 审计 |

**内部流程（模板 Plan）**：

```
POST /vision/analyze
    → 存储 MinIO
    → [vision_analyze] VLM 识别判断（每图 1 次）
    → [kb_regulation] 按命中 type 检索规章
    → [summarize] 汇总 → 返回 VisionAnalyzeResponse
```

**关键词与检测项映射**（配置化，避免硬编码）：

```yaml
# config/vision_scenes.yaml
scenes:
  no_helmet: ["未戴安全帽", "安全帽", "头盔"]
  smoking: ["抽烟", "吸烟"]
  phone_use: ["玩手机", "看手机"]
```

---

### 3.3 文本/语音任务编排模块（Agent · 同一套图）

**职责**：理解自然语言（如「今天未戴安全帽的人有谁」），由 **Planner** 生成步骤列表，**Executor** 按序调用 Skill（查告警 →  enrich 人员 → KB → 汇总）。与视觉链路共用 **§3.6** 主图，仅入口与可用 Skill 集合不同。

| 组件 | 推荐选型 | 理由 |
|------|----------|------|
| 编排内核 | **LangGraph** + **Plan-Execute** | 规划与执行解耦，步骤可审计、可单测 |
| Skill 形态 | **子图（Subgraph）** + 少量 `@tool` | 图片识别用子图保状态；SQL 查询可用 tool |
| 规划 LLM | **通义千问-Max** / **DeepSeek-V3** / **GLM-4** | 生成 `PlanStep[]` |
| 执行 LLM | 可与规划同模型或更小模型 | 仅做步骤内推理/汇总 |
| 会话记忆 | Redis + `thread_id` | 多轮对话、计划复用 |
| SQL 安全 | 只读账号 + 白名单视图/存储过程 | Skill 内封装，Planner 不可直接写 SQL |

**与视觉共用的 Skill 注册表（节选）**：

| Skill ID | 功能 | 实现建议 |
|----------|------|----------|
| `alert_query` | 按时间/类型查告警 | Tool + 参数 schema |
| `person_enrich` | 人员、班组信息 | Tool |
| `kb_regulation` | 规章制度 RAG | Subgraph 或 Adapter 调用 |
| `vision_analyze` | 图片识别判断（VLM，每图 ≤1 次） | **Subgraph** |
| `summarize` | 最终答复与结构化输出 | 节点函数 |

**文本任务 Plan 示例（Planner 输出）**：

```json
{
  "steps": [
    {"skill": "alert_query", "args": {"date": "today", "violation_type": "no_helmet"}},
    {"skill": "person_enrich", "args": {"from": "step_0.person_ids"}},
    {"skill": "kb_regulation", "args": {"query": "未佩戴安全帽 处罚规定"}},
    {"skill": "summarize", "args": {"style": "list_with_regulations"}}
  ]
}
```

---

### 3.4 语音输入模块

**职责**：接收前端录音或音频文件，转文本后进入 Agent 或直接返回转写结果。

| 组件 | 推荐选型 | 备选 |
|------|----------|------|
| 云端 ASR | **阿里云智能语音**、**讯飞开放平台** | 中文场景准确率高 |
| 开源 ASR | **FunASR**（Paraformer） | 可私有化、中文友好 |
| 通用开源 | **OpenAI Whisper** / **faster-whisper** | 多语言、部署简单 |
| 音频格式 | 前端 **WebM/MP3** → 后端 **ffmpeg** 转 16k WAV | 统一 ASR 输入 |
| 流式（可选） | FunASR 流式 / 讯飞实时 | 边说边出字 |

**流程**：`音频上传 → ffmpeg 归一化 → ASR → text → POST /agent/query`

前端可选用 **MediaRecorder API** 录音上传；浏览器端 **Web Speech API** 仅作辅助（兼容性差），生产以服务端 ASR 为准。

---

### 3.5 知识库模块（开源、可替换）

**职责**：存储安全生产规章制度 PDF/Word，支持语义检索，为视觉判定与 Agent 回答提供条文依据。

采用 **适配器模式（KB Adapter）**，业务代码只依赖统一接口，便于更换底层产品。

```python
# 统一接口（示意）
class KnowledgeBaseAdapter(Protocol):
    async def ingest(self, file_path: str, metadata: dict) -> str: ...
    async def search(self, query: str, top_k: int = 5) -> list[RegulationChunk]: ...
```

**开源知识库平台对比（自主选型）**：

| 平台 | 特点 | 推荐场景 |
|------|------|----------|
| **[RAGFlow](https://github.com/infiniflow/ragflow)** | 深度文档解析、RAG 效果好、可 Docker 部署 | **首选**：规章 PDF 多、要 citation |
| **[Dify](https://github.com/langgenius/dify)** | 知识库 + Agent 工作流一体 | 想低代码配工作流时 |
| **[MaxKB](https://github.com/1Panel-dev/MaxKB)** | 轻量、中文友好、1Panel 生态 | 中小规模、快速上线 |
| **[FastGPT](https://github.com/labring/FastGPT)** | 工作流 + 知识库 | 已有 FastGPT 运维经验 |
| **自建：Qdrant/Milvus + LangChain** | 完全自控 | 团队有向量库运维能力 |

**推荐组合（平衡效果与运维）**：

- 知识库引擎：**RAGFlow**（或 MaxKB）
- Embedding：**BGE-M3** / **text-embedding-v3**（千问）
- 重排序：**bge-reranker-v2-m3**
- 接入方式：HTTP API 封装为 `RagflowKbAdapter`

---

### 3.6 LangGraph + Skill + Plan-Execute 统一编排（推荐）

**结论：可以，且推荐作为后端核心架构。** 图片识别 API 与文本/语音智能体编排共用同一套 LangGraph 主图，通过 **Skill 注册表** 暴露能力，用 **Plan-Execute** 分离「做什么」与「怎么做」。

#### 3.6.1 为何适合 KSAgent

| 诉求 | Plan-Execute + Skill 的匹配点 |
|------|------------------------------|
| 图片识别 API | 对外统一接口；对内 `vision_analyze` Skill 调 VLM，**每图 ≤1 次** |
| 文本「今天谁未戴帽」 | Planner 动态生成 `alert_query → person_enrich → kb → summarize` |
| 规章联动 | `kb_regulation` Skill 被视觉与文本计划复用 |
| 可观测/合规 | `plan` 与每步 `step_result` 落库，便于审计与人工复核 |
| 扩展 | 新增检测场景 = 注册 Skill + 更新 Planner 提示，不必改 API 契约 |

#### 3.6.2 概念分层

```
┌─────────────────────────────────────────────────────────────┐
│  API 层（FastAPI）                                            │
│  /vision/analyze  /agent/query  /speech/transcribe           │
└────────────────────────────┬────────────────────────────────┘
                             │ 构造 GraphInput，指定 entry + skill_filter
┌────────────────────────────▼────────────────────────────────┐
│  主图：ksagent_orchestrator（Plan-Execute）                   │
│  ┌─────────┐   ┌─────────┐   ┌──────────┐   ┌────────────┐ │
│  │ router  │ → │  plan   │ → │ execute  │ → │ synthesize │ │
│  └─────────┘   └────┬────┘   └────┬─────┘   └────────────┘ │
│                     │             │                          │
│                     │      ┌──────▼──────┐ 失败/缺参         │
│                     │      │ replan    │◄─────────────────┘ │
│                     │      └─────────────┘                   │
└─────────────────────┼────────────────────────────────────────┘
                      │ 只引用 Skill 元数据（id/description/schema）
┌─────────────────────▼────────────────────────────────────────┐
│  Skill Registry（注册表）                                      │
│  每个 Skill：id、描述、参数 schema、实现（子图 | tool）       │
└─────────────────────┬────────────────────────────────────────┘
        ┌─────────────┼─────────────┬──────────────┐
        ▼             ▼             ▼              ▼
  vision_analyze   kb_regulation   alert_query   summarize
  (VLM 识别判断)    (规章 RAG)       (Tool)        (节点)
```

**Skill 定义原则**：

- **Subgraph**：有内部状态、多节点（VLM 识别判断、KB 多路检索）。
- **Tool**：单步、IO 清晰（SQL 查询、统计聚合）。
- Skill 对外只暴露统一入口节点 `skill_invoke(state, step_args) -> partial state`。

#### 3.6.3 Plan-Execute 主图节点说明

| 节点 | 职责 | 输入/输出 |
|------|------|-----------|
| `router` | 区分 `vision` / `agent` / `speech` 入口，设置 `skill_filter` | 图片 API 限制为 `vision_analyze` + KB + summarize |
| `plan` | 调用规划 LLM，输出 `PlanStep[]`（结构化 JSON） | 可读 Skill Registry 描述，禁止编造未注册 Skill |
| `execute` | 取 `plan[current]`，派发到 Registry 中对应 Skill | 写入 `artifacts[step_id]` |
| `replan` | 某步失败、结果为空、置信度不足时，追加或调整后续步骤 | 最多 N 次，防止死循环 |
| `synthesize` | 汇总 `artifacts`，生成 API 响应 | `VisionAnalyzeResponse` / `AgentQueryResponse` |

**Graph State（建议 TypedDict / Pydantic）**：

```python
class OrchestratorState(TypedDict):
    request_id: str
    entry: Literal["vision", "agent", "speech"]
    messages: list[AnyMessage]
    # 视觉
    image_url: str | None
    keywords: list[str] | None
    # 编排
    plan: list[PlanStep]
    current_step: int
    artifacts: dict[str, Any]   # step_0, step_1, ...
    # 控制
    skill_filter: list[str] | None
    replan_count: int
    final_response: dict | None
```

#### 3.6.4 `vision_analyze` Skill（图片识别判断）

KSAgent **不承载、不描述** 外部初判/检测链路；本 Skill 为图片识别 API 的核心实现。

1. 加载 `vision_scenes.yaml`，将 `keywords` 映射为检测关注点  
2. 经 `VlmClient.analyze(image, scenes, strict)` 调用多模态大模型（**每 `request_id` 最多 1 次**，节点内 `vlm_invoked` 防重入）  
3. 解析为 `VisionAnalyzeResult`：`violations[]` 含 `type, confidence, description, bbox?`  
4. 写入 `artifacts.vision`；供后续 `kb_regulation`、`summarize` 使用  

**VLM 选型**：通义千问-VL / GLM-4V（API）或 Qwen2-VL + vLLM（私有化）。Prompt 模板见 `config/vision_analyze_prompt.yaml`。

#### 3.6.5 两类入口的 Plan 策略

| 入口 | Plan 生成方式 | 说明 |
|------|---------------|------|
| `POST /vision/analyze` | **模板计划** | 固定 `[vision_analyze, kb_regulation, summarize]`，无 LLM Planner |
| `POST /agent/query` | **LLM Planner** 动态生成 | 根据自然语言选择 Skill 与参数 |
| 语音 | ASR 前置节点 → 转入 `agent` 入口 | 与文本共用 Planner |

视觉接口不强制每次走 LLM Planner，可避免多一次 LLM 调用；**图结构仍是 Plan-Execute**，只是 `plan` 节点对 vision 入口走模板分支。

#### 3.6.6 LangGraph 实现要点（Python）

```python
# 结构示意，非完整可运行代码
from langgraph.graph import StateGraph, END

def build_orchestrator(registry: SkillRegistry):
    g = StateGraph(OrchestratorState)
    g.add_node("router", router_node)
    g.add_node("plan", plan_node)           # vision 入口可走 plan_from_template
    g.add_node("execute", execute_node)     # registry.invoke(skill_id, ...)
    g.add_node("replan", replan_node)
    g.add_node("synthesize", synthesize_node)

    g.set_entry_point("router")
    g.add_edge("router", "plan")
    g.add_conditional_edges(
        "execute",
        route_after_execute,  # 还有步骤? -> execute ; 失败? -> replan ; 完成 -> synthesize
    )
    g.add_conditional_edges("replan", lambda s: "execute" if s["plan"] else "synthesize")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=redis_checkpointer)  # thread_id 持久化
```

- **子图挂载**：`registry.register("vision_analyze", build_vision_analyze_graph().compile())`  
- **与 LangChain Tool 关系**：Tool 可作为 Skill 的薄封装；Planner 只见 Skill 元数据，不见实现细节  
- **参考**：LangGraph 官方 [Plan-and-Execute](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/) 教程，本项目将「Executor」替换为 **Skill 派发器**

#### 3.6.7 与「纯 ReAct 单循环」对比

| 维度 | ReAct 单循环 | Plan-Execute + Skill |
|------|--------------|----------------------|
| 图片识别 | 易重复调 VLM 或步骤不透明 | 固定模板 Plan + 每图 VLM ≤1 次 |
| 可测试性 | 依赖 LLM 即兴 | 可对固定 Plan 做集成测试 |
| 审计 | 仅最终消息 | `plan` + `artifacts` 分步留痕 |
| 延迟 | 轮次不确定 | 视觉模板计划步数固定 |

#### 3.6.8 注意事项

1. **Planner 幻觉**：`plan` 输出须经 Pydantic 校验，Skill ID 必须在 Registry 内。  
2. **VLM 成本**：`vision_analyze` 内禁止二次调用；可按 `request_id` 缓存结果。  
3. **幂等**：`request_id` + 图片 hash 防重复推理。  
4. **人机协同**：`needs_human_review` 写入 `vision_records`，供前端标注回流。

---

### 3.7 数据层

**PostgreSQL 核心表（概念模型）**：

| 表 | 用途 |
|----|------|
| `alerts` | 告警事件：时间、类型、摄像头、截图 URL、人员 ID |
| `persons` | 人员：姓名、工号、班组 |
| `vision_records` | 图片识别 API 请求、VLM 原始输出、最终 violations |
| `agent_sessions` | 会话、消息历史（或放 Redis） |
| `regulation_refs` | 可选：本地缓存 KB 返回的条文快照 |

**Redis**：JWT 黑名单、API 限流、Agent `thread_id` 状态。

**MinIO**：`images/{date}/{uuid}.jpg`、`audio/{uuid}.wav`。

---

## 4. 前端架构（Vue 3）

```
frontend/
├── src/
│   ├── api/              # Axios 封装，按模块划分
│   ├── views/
│   │   ├── VisionAnalyze.vue    # 图片上传 + 关键词 + 结果
│   │   ├── AgentChat.vue        # 文本任务对话
│   │   └── SpeechInput.vue      # 录音组件（可嵌入 Chat）
│   ├── components/
│   │   ├── ImageUploader.vue
│   │   ├── ViolationCard.vue    # 违规项 + 规章卡片
│   │   └── AudioRecorder.vue
│   ├── stores/           # Pinia
│   ├── router/
│   └── types/            # 与后端 Pydantic 对齐的 TS 类型
```

| 能力 | 选型 |
|------|------|
| 路由 | **Vue Router 4** |
| 请求 | **Axios** + 统一 `ApiResponse<T>` |
| 图片预览 | `el-upload` + 裁剪（可选 `vue-cropper`） |
| 对话 UI | 自研或参考 `@chatui/core` 样式 |
| 环境变量 | `.env.development` / `.env.production` |

---

## 5. 后端项目结构（Python）

```
backend/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── api/v1/
│   │   ├── vision.py
│   │   ├── agent.py
│   │   ├── speech.py
│   │   └── kb.py
│   ├── services/
│   │   ├── orchestrator_service.py  # 统一入口，compile 主图
│   │   ├── speech_service.py
│   │   └── kb/
│   │       ├── base.py
│   │       ├── ragflow_adapter.py
│   │       └── maxkb_adapter.py
│   ├── skills/
│   │   ├── registry.py
│   │   ├── vision_analyze.py     # 图片识别判断（VlmClient）
│   │   ├── kb_regulation.py
│   │   ├── alert_query.py
│   │   └── summarize.py
│   ├── models/
│   ├── schemas/
│   │   ├── plan.py               # PlanStep, PlanOutput
│   │   └── vision.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── llm_client.py         # 文本 LLM（Planner）
│   │   └── vlm_client.py         # 仅 vision_analyze 使用
│   └── graphs/
│       ├── orchestrator.py       # Plan-Execute 主图
│       ├── nodes/
│       │   ├── router.py
│       │   ├── plan.py           # LLM plan + template plan
│       │   ├── execute.py
│       │   ├── replan.py
│       │   └── synthesize.py
│       └── checkpointer.py       # Redis / Postgres saver
├── alembic/
├── config/
│   ├── vision_scenes.yaml
│   └── vision_analyze_prompt.yaml
├── tests/
├── pyproject.toml                # 推荐 uv/poetry
└── Dockerfile
```

**主要 Python 依赖**：

```
fastapi, uvicorn, sqlalchemy, alembic, pydantic-settings
langgraph, langchain-openai（或 langchain-community 对接国产模型）
httpx, redis, minio, pillow
python-jose, passlib
celery（可选）
```

---

## 6. 部署架构

**开发环境**：Docker Compose 一键拉起

- `frontend`（Vite dev 或 nginx 静态）
- `backend`（Uvicorn --reload）
- `postgres`、`redis`、`minio`
- `ragflow`（或所选 KB）独立 compose profile

**生产环境**：

```
Internet → Nginx (TLS)
    → /api/*  → backend × N (Gunicorn+Uvicorn workers)
    → /*      → frontend 静态
    → /minio/* → 内网 MinIO（不对外暴露）
```

- 密钥：`.env` + Docker secrets / K8s Secret
- 日志：**结构化 JSON** → Loki / ELK
- 监控：Prometheus + Grafana（API 延迟、VLM 调用耗时）

---

## 7. 非功能需求

| 维度 | 方案 |
|------|------|
| 安全 | JWT、上传文件类型白名单、SQL 只读、敏感配置不入库 |
| 性能 | 图片压缩后送 VLM；KB 结果缓存 |
| 可观测 | `request_id` 全链路；记录 `vlm_analyze_latency`、`vlm_invoked` |
| 扩展 | KB Adapter / LLM Client 抽象，便于换模型与换 RAG 产品 |
| 合规 | 识别结果落库留痕；规章引用保留原文片段与出处 |

---

## 8. 实施阶段建议

| 阶段 | 交付 |
|------|------|
| **P0** | 图片识别 API + `vision_analyze` Skill + 模板 Plan；Vue 上传 |
| **P1** | `kb_regulation` + `vision_scenes.yaml`；规章引用返回 |
| **P2** | PostgreSQL 告警 + LLM Planner 文本任务；`vision_records` 审计 |
| **P3** | 语音接入、VLM 缓存与熔断、私有化部署、LangGraph 可观测 |

---

## 9. 架构决策记录（ADR 摘要）

1. **前后端分离**：Vue3 专注交互，Python 专注 AI 与数据，职责清晰。
2. **FastAPI 而非 Django**：AI 路由以 API 为主，async 友好，OpenAPI 开箱即用。
3. **LangGraph + Skill + Plan-Execute 统一编排**：图片识别 API 与文本 Agent 共用主图；视觉走固定模板 Plan。
4. **图片识别只暴露 API**：初判/检测不在本架构范围；对内由 `vision_analyze` 调 VLM（每图 ≤1 次）。
5. **知识库产品化而非从零向量库**：规章 PDF 解析成本高，RAGFlow/MaxKB 缩短交付周期。
6. **适配器隔离 KB 与 VLM**：`VlmClient` / `KbAdapter` 互不耦合。

---

## 10. 与 README 功能映射

| README 功能 | 架构落点 |
|-------------|----------|
| Python + Vue3 | 全栈 §2、§4、§5 |
| Web 接口 | §3.1 FastAPI REST |
| 图片+关键词识别 | §3.2 图片识别 API + §3.6 `vision_analyze` Skill |
| 文本任务编排 | §3.3 + §3.6 Plan-Execute 主图 + 数据类 Skill |
| 语音输入 | §3.4 ASR → 同一 orchestrator `entry=speech` |
| 知识库 | §3.5 `kb_regulation` Skill |
| 统一编排 | §3.6 LangGraph + Skill Registry + Plan-Execute |
