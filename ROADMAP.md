# KSAgent 后期任务规划

> **版本**: v1.0 (2026-06-04)  
> **状态**: 阶段2已完成，规划阶段3-5  
> **范围**: 知识库集成、Agent-of-Agent、生产优化

---

## 规划概述

本文档规划 KSAgent 智能体平台后续开发任务，包括：
1. **阶段3**: RAG 知识库集成（规章制度联动）
2. **阶段4**: Agent-of-Agent 元智能体平台化
3. **阶段5**: 生产优化与完善

---

## 阶段3: RAG 知识库集成

### 3.1 目标

实现规章制度知识库查询，让智能体能引用具体条文回答问题。

**用例**:
- "未戴安全帽违反哪些规定？" → 返回具体条文
- 图片识别发现违规 → 自动联动知识库返回相关规章

### 3.2 知识库选型

#### 3.2.1 候选方案对比

| 平台 | 特点 | 优势 | 劣势 | 推荐度 |
|------|------|------|------|--------|
| **RAGFlow** | 深度文档解析、RAG 效果好 | ✅ PDF 解析强<br>✅ Citation 完整<br>✅ 私有化部署 | ❌ 部署略重 | ⭐⭐⭐⭐⭐ |
| **MaxKB** | 轻量、中文友好 | ✅ 部署简单<br>✅ 1Panel 生态<br>✅ 中小规模适用 | ❌ 高级 RAG 功能少 | ⭐⭐⭐⭐ |
| **Dify** | 知识库 + Agent 工作流 | ✅ 低代码配置<br>✅ 社区活跃 | ❌ 与现有架构重叠 | ⭐⭐⭐ |
| **自建 (Qdrant + LangChain)** | 完全自控 | ✅ 灵活定制<br>✅ 无黑盒 | ❌ 开发成本高<br>❌ 需运维向量库 | ⭐⭐ |

#### 3.2.2 推荐方案

**首选**: **RAGFlow** (https://github.com/infiniflow/ragflow)

**理由**:
1. ✅ **文档解析能力强**: 规章制度多为复杂 PDF，RAGFlow 的深度解析效果好
2. ✅ **Citation 支持**: 能返回原文片段和页码，便于审计
3. ✅ **私有化部署**: Docker Compose 一键拉起
4. ✅ **API 友好**: RESTful API 易于集成
5. ✅ **成熟度高**: 商业级产品开源，稳定性好

**备选**: **MaxKB** (轻量场景)

### 3.3 集成架构

#### 3.3.1 架构设计

```
┌─────────────────────────────────────────────────────┐
│  LangGraph 编排层                                    │
│  Planner → Executor → Formatter                     │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│  Skill Registry                                     │
│  ┌────────────────────────────────────────────┐    │
│  │ kb_regulation (Skill)                      │    │
│  │ - 语义检索规章制度                          │    │
│  │ - 返回条文片段 + 页码                       │    │
│  └────────────┬───────────────────────────────┘    │
└───────────────┼────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────┐
│  KB Adapter (适配器模式)                            │
│  - RagflowAdapter: 封装 RAGFlow API                │
│  - MaxKBAdapter: 封装 MaxKB API (备选)             │
└───────────────┬────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────┐
│  RAGFlow 知识库                                     │
│  - Dataset: safety_regulations                     │
│  - Embedding: bge-m3                               │
│  - Reranker: bge-reranker-v2-m3                    │
└────────────────────────────────────────────────────┘
```

#### 3.3.2 适配器接口设计

```python
# utils/kb/base.py
class KBAdapter(Protocol):
    """知识库适配器统一接口"""
    
    async def ingest(self, file_path: str, metadata: dict) -> str:
        """上传文档到知识库
        
        Args:
            file_path: 文档路径 (PDF/Word)
            metadata: 元数据 (category, version, etc.)
        
        Returns:
            doc_id: 文档唯一标识
        """
        ...
    
    async def search(self, query: str, top_k: int = 5, 
                    filters: dict = None) -> list[RegulationChunk]:
        """语义检索
        
        Args:
            query: 查询文本
            top_k: 返回结果数
            filters: 过滤条件 (category, date_range, etc.)
        
        Returns:
            chunks: 条文片段列表
        """
        ...
    
    async def delete(self, doc_id: str) -> bool:
        """删除文档"""
        ...


@dataclass
class RegulationChunk:
    """规章条文片段"""
    doc_id: str              # 文档ID
    title: str               # 文档标题
    content: str             # 条文内容
    page: int | None         # 页码
    score: float             # 相似度分数
    metadata: dict           # 元数据
```

#### 3.3.3 RAGFlow 适配器实现

```python
# utils/kb/ragflow_adapter.py
class RagflowAdapter:
    def __init__(self, base_url: str, api_key: str, dataset_id: str):
        self.base_url = base_url
        self.api_key = api_key
        self.dataset_id = dataset_id
        self.client = httpx.AsyncClient()
    
    async def ingest(self, file_path: str, metadata: dict) -> str:
        """上传文档到 RAGFlow"""
        url = f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents"
        
        with open(file_path, "rb") as f:
            files = {"file": f}
            data = {"metadata": json.dumps(metadata)}
            
            response = await self.client.post(
                url,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        
        result = response.json()
        return result["data"]["document_id"]
    
    async def search(self, query: str, top_k: int = 5, 
                    filters: dict = None) -> list[RegulationChunk]:
        """检索"""
        url = f"{self.base_url}/api/v1/datasets/{self.dataset_id}/retrieval"
        
        payload = {
            "question": query,
            "top_k": top_k,
            "similarity_threshold": 0.5
        }
        
        if filters:
            payload["filter"] = filters
        
        response = await self.client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        
        result = response.json()
        
        chunks = []
        for item in result["data"]["chunks"]:
            chunks.append(RegulationChunk(
                doc_id=item["document_id"],
                title=item["document_name"],
                content=item["content"],
                page=item.get("page"),
                score=item["score"],
                metadata=item.get("metadata", {})
            ))
        
        return chunks
```

### 3.4 Skill 注册

```python
# skills/kb_skill.py
async def kb_regulation_impl(args: dict, context: dict) -> dict:
    """规章制度检索 Skill"""
    query = args["query"]
    top_k = args.get("top_k", 5)
    
    # 获取 KB Adapter
    kb_adapter = get_kb_adapter()  # 从配置读取
    
    try:
        chunks = await kb_adapter.search(query, top_k)
        
        return {
            "regulations": [
                {
                    "title": chunk.title,
                    "content": chunk.content,
                    "page": chunk.page,
                    "score": chunk.score
                }
                for chunk in chunks
            ],
            "total": len(chunks)
        }
    except Exception as e:
        return {"error": f"KB search failed: {e}"}


# 注册到 Registry
def register_kb_skill(registry: SkillRegistry):
    skill = Skill(
        id="kb_regulation",
        name="规章制度检索",
        description="检索安全生产规章制度，返回相关条文",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询关键词（如：未戴安全帽）"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5
                }
            },
            "required": ["query"]
        },
        implementation=kb_regulation_impl,
        skill_type=SkillType.TOOL,
        tags=["knowledge", "regulation"]
    )
    registry.register(skill)
```

### 3.5 配置文件

```yaml
# config.yaml
rag:
  enabled: true
  adapter: ragflow  # 或 maxkb
  
  ragflow:
    base_url: "http://ragflow.internal:8080"
    api_key: "YOUR_API_KEY"
    dataset_id: "safety_regulations"
    embedding_model: "bge-m3"
    reranker_model: "bge-reranker-v2-m3"
  
  maxkb:  # 备选
    base_url: "http://maxkb.internal:8080"
    api_key: "YOUR_API_KEY"
    dataset_id: "safety_regs"
```

### 3.6 实施步骤

#### P0: RAGFlow 部署 (1周)
- [ ] Docker Compose 部署 RAGFlow
- [ ] 上传测试文档（5-10份规章制度 PDF）
- [ ] 调试检索效果（调整 chunk size, overlap）
- [ ] 测试 API 调用

#### P1: 适配器开发 (3天)
- [ ] 实现 `RagflowAdapter`
- [ ] 单元测试（ingest, search, delete）
- [ ] 配置化切换（ragflow/maxkb）

#### P2: Skill 注册 (2天)
- [ ] 实现 `kb_regulation` Skill
- [ ] 注册到 Skill Registry
- [ ] 测试 Planner 能发现并调用

#### P3: 端到端测试 (2天)
- [ ] 文本查询："未戴安全帽违反哪些规定？"
- [ ] 图片识别 + KB 联动（阶段4）
- [ ] 验证 Citation 完整性

#### P4: 文档管理 (1天)
- [ ] 规章制度上传界面（可选）
- [ ] 文档版本管理
- [ ] 审计日志

---

## 阶段4: Agent-of-Agent 元智能体

### 4.1 目标

实现元智能体平台，让 Claude 根据需求自动生成、测试、发布专属智能体。

**核心能力**:
1. 自然语言描述需求 → 自动生成 Agent 代码
2. 自动验收测试（执行、评估、反馈）
3. 通过验收后发布到注册表
4. 动态加载已发布 Agent，提供 API 服务

### 4.2 当前实现状态

**已完成**:
- ✅ 元智能体核心模块 (`agent/meta_agent/`)
  - `spec_parser.py` - 需求规范解析
  - `prompt_generator.py` - 提示词生成
  - `code_generator.py` - 代码生成
  - `executor.py` - 测试执行
  - `evaluator.py` - 验收评估
  - `feedback_analyzer.py` - 反馈分析（支持迭代优化）
  - `llm_client.py` - LLM 客户端（Claude/IMDS）
  - `tool_impl.py` - 工具实现（**待迁移**到 Skill Registry）
- ✅ Agent 注册表 (`agent/registry.py`)：`publish` / `unpublish` / `load_agent_run`
- ✅ 发布机制 (`agent/publish.py`)
- ✅ 端到端首跑通过（详见 memory/agent_of_agent_implementation.md）

**待完善**:
- [ ] **迁移 `tool_impl.py` 到 Skill Registry**（核心改造点，参见 4.4.1）
- [ ] 创建 `RULES.md`（生成代码规则约束，目前不存在该文件）
- [ ] 与 Skill Registry 集成（生成的 Agent 使用 Registry，而非 `tool_impl`）
- [ ] Web 界面（创建/管理 Agent）
- [ ] 热重载已发布 Agent
- [ ] Agent 版本管理与灰度发布

### 4.3 架构集成

```
┌─────────────────────────────────────────────────────┐
│  用户需求描述                                        │
│  "我需要一个查询本周告警统计的智能体"                 │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│  Meta Agent (元智能体)                               │
│  ┌────────┐   ┌────────┐   ┌────────┐              │
│  │Generator│→ │Executor│→ │Evaluator│              │
│  │ 生成   │   │ 测试   │   │ 验收   │              │
│  └────────┘   └────────┘   └────────┘              │
└────────────┬────────────────────────────────────────┘
             │ 生成的 Agent 代码
             │ ✅ 使用 Skill Registry
             │ ✅ 不直接访问数据库
             │
┌────────────▼────────────────────────────────────────┐
│  Agent Registry (智能体注册表)                       │
│  {                                                  │
│    "weekly_alarm_stats": {                         │
│      "version": "1.0.0",                           │
│      "route": "/agents/weekly_alarm_stats/chat",   │
│      "published_path": "artifacts/published/..."   │
│    }                                               │
│  }                                                 │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│  FastAPI 动态路由                                    │
│  POST /agents/weekly_alarm_stats/chat              │
│  → 动态加载 Agent.run()                             │
│  → 返回响应                                         │
└────────────────────────────────────────────────────┘
```

### 4.4 关键改进

#### 4.4.1 生成的 Agent 使用 Skill Registry

**当前问题**: `RULES.md` 指导生成的代码仍使用 `tool_impl` 直接访问数据库

**改进方案**: 更新 `RULES.md` 和代码模板

```python
# agent/meta_agent/templates/agent_template.py
"""
{agent_name} - {description}

自动生成于 {timestamp}
"""
from skills import get_skill_registry

async def run(user_input: str) -> str:
    """Agent 入口函数"""
    registry = get_skill_registry()
    
    # 示例：调用 Skill
    result = await registry.invoke(
        skill_id="query_alarms",
        args={"date": "2026-06-04"},
        context={"agent": "{agent_name}"}
    )
    
    if result.get("error"):
        return f"查询失败: {result['error']}"
    
    # 处理结果
    alarms = result.get("alarms", [])
    return f"查询到 {len(alarms)} 条告警"
```

#### 4.4.2 创建并维护 RULES.md

**当前状态**: 项目中**尚不存在** `agent/meta_agent/RULES.md` 文件，元智能体的生成约束目前散落在 `prompt_generator.py` 和 `templates/prompt_library/` 中。

**改造目标**: 抽离独立的 `RULES.md`，作为代码生成规则的单一事实源。

```markdown
# agent/meta_agent/RULES.md (新建)

## 11. 数据访问规则（重要）

**禁止**:
- ❌ 不要导入 `agent/meta_agent/tool_impl.py`
- ❌ 不要直接写 SQL 查询
- ❌ 不要直接访问数据库

**必须**:
- ✅ 使用 Skill Registry 调用工具
- ✅ 代码模板:
  ```python
  from skills import get_skill_registry
  
  registry = get_skill_registry()
  result = await registry.invoke("query_alarms", args)
  ```

**可用的 Skill**:
- `query_alarms`: 查询告警记录
- `query_person`: 查询人员信息
- `query_video`: 查询录像片段
- `kb_regulation`: 检索规章制度（阶段3）

**如何查看可用 Skill**:
```python
skills = registry.list_skills()
for skill in skills:
    print(f"{skill.id}: {skill.description}")
```
```

#### 4.4.3 Web 界面集成

**功能列表**:
1. **创建 Agent**: 输入需求描述 → 生成 → 测试 → 发布
2. **管理 Agent**: 列表、详情、删除、重新发布
3. **测试 Agent**: 在线测试已发布的 Agent
4. **监控**: 调用次数、成功率、平均延迟

**技术栈**:
- 前端: Vue 3 + Element Plus
- 后端: FastAPI 路由
- 状态管理: Agent Registry (JSON)

### 4.5 实施步骤

#### P0: 集成 Skill Registry (3天)
- [ ] 更新 `RULES.md`（禁止直接访问数据库）
- [ ] 更新代码模板（使用 Registry）
- [ ] 测试生成的 Agent 能正确调用 Skill

#### P1: 动态路由 (2天)
- [ ] FastAPI 动态注册路由
  ```python
  @app.post("/agents/{agent_name}/chat")
  async def agent_chat(agent_name: str, request: ChatRequest):
      run_func = load_agent_run(agent_name)
      response = await run_func(request.message)
      return {"response": response}
  ```
- [ ] 热重载机制（检测注册表变化）

#### P2: Web 界面 (5天)
- [ ] 创建 Agent 页面
- [ ] Agent 列表页面
- [ ] 测试对话页面
- [ ] 监控仪表板

#### P3: 版本管理 (3天)
- [ ] Agent 多版本支持
- [ ] 灰度发布（v1 → v2）
- [ ] 回滚机制

---

## 阶段5: 生产优化

### 5.1 性能优化

#### 5.1.1 MCP 调用优化
- [ ] 完整启用 stdio 协议（替换临时方案）
- [ ] 连接池管理
- [ ] 调用缓存（Redis）

#### 5.1.2 LLM 调用优化
- [ ] Prompt 优化（减少 token）
- [ ] 流式输出（WebSocket）
- [ ] 并发控制（限流）

#### 5.1.3 并发处理
- [ ] 异步任务队列（Celery）
- [ ] 批量处理
- [ ] 结果缓存

### 5.2 监控与告警

#### 5.2.1 Metrics
```python
# Prometheus metrics
mcp_call_duration = Histogram("mcp_call_duration_seconds")
skill_invoke_total = Counter("skill_invoke_total", ["skill_id", "status"])
llm_token_usage = Counter("llm_token_usage", ["model"])
```

#### 5.2.2 日志
- 结构化日志 (JSON)
- 链路追踪 (trace_id)
- 审计日志 (SQLite)

#### 5.2.3 告警
- API 错误率 > 5%
- MCP 调用延迟 > 100ms
- LLM 调用失败

### 5.3 安全加固

#### 5.3.1 认证授权
- [ ] JWT 认证
- [ ] API Key 管理
- [ ] 权限分级（admin/user/readonly）

#### 5.3.2 数据安全
- [ ] 敏感字段加密
- [ ] SQL 注入防护（MCP 层）
- [ ] 输入校验

### 5.4 文档完善

- [ ] API 文档（Swagger）
- [ ] 开发者文档（本系列文档）
- [ ] 运维手册
- [ ] 故障排查指南

---

## 总体时间表

| 阶段 | 任务 | 工作量 | 依赖 |
|------|------|--------|------|
| **阶段3** | RAG 知识库集成 | 2周 | - |
| **阶段4** | Agent-of-Agent | 2周 | 阶段3 |
| **阶段5** | 生产优化 | 3周 | 阶段3, 4 |

**总计**: 7周（约1.5个月）

---

## 风险与挑战

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| RAGFlow 解析效果不佳 | 高 | 提前测试，准备 MaxKB 备选 |
| Agent-of-Agent 生成代码质量 | 中 | 严格验收测试，人工审核 |
| 性能瓶颈 | 中 | 渐进式优化，监控先行 |
| MCP stdio 调试困难 | 低 | 保留临时方案，不阻塞 |

---

## 成功标准

### 阶段3 验收
- [ ] 用户询问"未戴安全帽违反哪些规定？"能返回具体条文
- [ ] 图片识别 + KB 联动流程跑通
- [ ] 检索准确率 > 80%（人工评估）

### 阶段4 验收
- [ ] 自然语言描述 → 生成 Agent → 自动测试 → 发布（端到端）
- [ ] 生成的 Agent 使用 Skill Registry（无直接数据库访问）
- [ ] Web 界面能管理 Agent

### 阶段5 验收
- [ ] API 响应 P95 < 3s
- [ ] 错误率 < 1%
- [ ] 监控仪表板上线
- [ ] 文档完善度 > 90%

---

**文档维护**: 随项目进展持续更新  
**最后更新**: 2026-06-04
