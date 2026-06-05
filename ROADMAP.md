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

| 方案 | 特点 | 优势 | 劣势 | 推荐度 |
|------|------|------|------|--------|
| **自建轻量方案** | Qdrant + Unstructured + FastEmbed | ✅ 轻量（< 2GB）<br>✅ 无冗余组件<br>✅ Python SDK 直接集成<br>✅ 完全可控 | ❌ 需自己实现文档管理<br>❌ 开发工作量 3-5 天 | ⭐⭐⭐⭐⭐ |
| **RAGFlow** | 全功能 RAG 平台 | ✅ PDF 解析强<br>✅ Citation 完整<br>✅ 开箱即用 | ❌ **部署冗余**（8-10GB）<br>❌ 包含工作流/UI 等不需要的功能 | ⭐⭐ |
| **MaxKB** | 轻量 KB 平台 | ✅ 部署相对简单<br>✅ 中文友好 | ❌ **仍然冗余**（6-8GB）<br>❌ 包含 Agent 等不需要的功能 | ⭐⭐ |
| **LightRAG** | 轻量知识图谱 RAG | ✅ 开箱即用<br>✅ 知识图谱增强 | ❌ 项目较新（2025）<br>❌ 生产案例少 | ⭐⭐⭐ |

#### 3.2.2 推荐方案

**首选**: **自建轻量方案** (Qdrant + Unstructured + FastEmbed)

**理由**:
1. ✅ **轻量化**: 资源占用 < 2GB（RAGFlow 需 8-10GB）
2. ✅ **无冗余**: 只有知识库核心功能（RAGFlow/MaxKB 包含工作流、UI 等不需要的模块）
3. ✅ **易集成**: Python SDK 直接调用，无需 HTTP 层（与 Skill Registry 无缝集成）
4. ✅ **完全可控**: 每个组件独立可替换（Embedding、重排序、向量库）
5. ✅ **文档解析强**: Unstructured 专为 RAG 设计，支持复杂 PDF 表格、多栏布局
6. ✅ **生产成熟**: Qdrant 已被大量生产环境验证

**为什么不推荐 RAGFlow/MaxKB？**
- ❌ **过于冗余**: 我们只需要知识库功能，但这些平台包含完整的智能体系统（工作流、对话、Agent 管理）
- ❌ **资源浪费**: 需要部署 5 个服务（Elasticsearch/MySQL/MinIO/Redis/前端），占用 8-10GB 内存
- ❌ **无法拆分**: 无法单独部署知识库模块，必须部署完整平台
- ❌ **架构冲突**: 我们已有 LangGraph 编排层，再部署一个完整平台会造成功能重叠

**备选**: 如果时间极度紧迫（< 1天），可考虑 **LightRAG** 快速验证

### 3.3 集成架构

#### 3.3.1 架构设计（自建轻量方案）

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
│  KB Service (Python 服务)                           │
│  ┌────────────────────────────────────────────┐    │
│  │ upload_document()  - 文档上传与解析        │    │
│  │ search()          - 语义检索 + 重排序      │    │
│  │ delete()          - 文档删除               │    │
│  └────────────┬───────────────────────────────┘    │
└───────────────┼────────────────────────────────────┘
                │
     ┌──────────┼──────────┬──────────┐
     │          │          │          │
┌────▼────┐ ┌──▼─────┐ ┌──▼─────┐ ┌──▼────────┐
│Qdrant   │ │Unstruct│ │FastEmbed│ │BGE-Rerank │
│(向量库) │ │(解析)  │ │(Embed) │ │(重排序)   │
└─────────┘ └────────┘ └────────┘ └───────────┘
```

#### 3.3.2 技术栈详解

**1. Qdrant（向量数据库）**
- **部署**: 单 Docker 容器
- **资源**: < 1GB 内存（百万级向量）
- **API**: gRPC + HTTP + Python SDK
- **特性**: 过滤、混合检索、payload 索引

**2. Unstructured（文档解析）**
- **能力**: 复杂 PDF 表格、多栏布局、阅读顺序保留
- **支持格式**: PDF、Word、PPT、HTML
- **策略**: `hi_res`（高精度，适合规章制度）

**3. FastEmbed（Embedding）**
- **模型**: BGE-M3（多语言、多任务）
- **优势**: 轻量、无需独立服务
- **集成**: Qdrant 官方库

**4. BGE-reranker-v2-m3（重排序）**
- **作用**: 精排 top_k 结果
- **库**: FlagEmbedding
- **支持**: 中英文跨语言

---

### 3.4 实现代码示例

#### 3.4.1 KB Service 实现

```python
# utils/kb/service.py
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from fastembed import TextEmbedding
from FlagEmbedding import FlagReranker
import uuid
from typing import List, Dict, Any

class KnowledgeBaseService:
    def __init__(self, qdrant_host: str = "localhost", qdrant_port: int = 6333):
        self.client = QdrantClient(host=qdrant_host, port=qdrant_port)
        self.embedding = TextEmbedding("BAAI/bge-m3")
        self.reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        self.collection_name = "safety_regulations"
        
        # 初始化 collection
        self._init_collection()
    
    def _init_collection(self):
        """初始化 Qdrant collection"""
        collections = self.client.get_collections().collections
        if self.collection_name not in [c.name for c in collections]:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
            )
    
    def upload_document(self, file_path: str, metadata: Dict[str, Any]) -> str:
        """上传文档到知识库
        
        Args:
            file_path: 文档路径 (PDF/Word)
            metadata: 元数据 (title, category, version, etc.)
        
        Returns:
            doc_id: 文档唯一标识
        """
        # 1. 文档解析
        loader = UnstructuredFileLoader(
            file_path,
            strategy="hi_res",  # 高精度解析
            mode="elements"     # 保留元素结构
        )
        docs = loader.load()
        
        # 2. 文本分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", "；", "，"]
        )
        chunks = splitter.split_documents(docs)
        
        # 3. Embedding
        texts = [chunk.page_content for chunk in chunks]
        vectors = list(self.embedding.embed(texts))
        
        # 4. 存储到 Qdrant
        doc_id = str(uuid.uuid4())
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "text": chunk.page_content,
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "metadata": metadata
                }
            )
            for i, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
        
        self.client.upsert(
            collection_name=self.collection_name,
            points=points
        )
        
        return doc_id
    
    def search(self, query: str, top_k: int = 5, filter_dict: Dict = None) -> List[Dict]:
        """语义检索
        
        Args:
            query: 查询文本
            top_k: 返回结果数
            filter_dict: 过滤条件 (如 {"metadata.category": "安全规定"})
        
        Returns:
            chunks: 条文片段列表
        """
        # 1. 向量检索（召回 20 条）
        query_vec = list(self.embedding.embed([query]))[0]
        
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vec,
            limit=20,
            query_filter=filter_dict
        )
        
        # 2. 重排序（精排 top_k）
        docs = [r.payload["text"] for r in results]
        pairs = [[query, doc] for doc in docs]
        scores = self.reranker.compute_score(pairs)
        
        # 3. 排序并返回
        reranked = sorted(
            zip(results, scores),
            key=lambda x: x[1],
            reverse=True
        )[:top_k]
        
        return [
            {
                "text": r.payload["text"],
                "score": float(score),
                "doc_id": r.payload["doc_id"],
                "chunk_index": r.payload["chunk_index"],
                "metadata": r.payload["metadata"]
            }
            for r, score in reranked
        ]
    
    def delete_document(self, doc_id: str) -> bool:
        """删除文档"""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector={"doc_id": doc_id}
        )
        return True
```

#### 3.4.2 Skill 注册

```python
# skills/knowledge_base.py
from utils.kb.service import KnowledgeBaseService
from skills.base import Skill, SkillType
from skills.registry import SkillRegistry

# 全局 KB Service 实例
kb_service = None

def get_kb_service() -> KnowledgeBaseService:
    global kb_service
    if kb_service is None:
        kb_service = KnowledgeBaseService()
    return kb_service


async def kb_regulation_impl(args: dict, context: dict) -> dict:
    """规章制度检索 Skill"""
    query = args["query"]
    top_k = args.get("top_k", 5)
    category = args.get("category")
    
    try:
        kb = get_kb_service()
        
        # 构造过滤条件
        filter_dict = None
        if category:
            filter_dict = {"metadata.category": category}
        
        results = kb.search(query, top_k, filter_dict)
        
        return {
            "regulations": [
                {
                    "title": r["metadata"].get("title", "未命名"),
                    "content": r["text"],
                    "page": r["metadata"].get("page"),
                    "score": r["score"],
                    "category": r["metadata"].get("category")
                }
                for r in results
            ],
            "total": len(results)
        }
    except Exception as e:
        return {"error": f"KB search failed: {e}"}


def register_kb_skill(registry: SkillRegistry):
    """注册知识库 Skill"""
    skill = Skill(
        id="kb_regulation",
        name="规章制度检索",
        description="检索安全生产规章制度文档，返回相关条文片段",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询关键词（如：未戴安全帽、抽烟违规）"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5
                },
                "category": {
                    "type": "string",
                    "description": "文档分类过滤（可选）",
                    "enum": ["安全规定", "操作规程", "应急预案"]
                }
            },
            "required": ["query"]
        },
        implementation=kb_regulation_impl,
        skill_type=SkillType.TOOL,
        tags=["knowledge", "regulation", "rag"]
    )
    registry.register(skill)
```

#### 3.4.3 FastAPI 文档管理接口（可选）

```python
# main.py 新增路由
from fastapi import FastAPI, UploadFile, File
from utils.kb.service import get_kb_service

app = FastAPI()

@app.post("/api/v1/kb/documents")
async def upload_kb_document(
    file: UploadFile = File(...),
    title: str = None,
    category: str = None
):
    """上传规章制度文档"""
    kb = get_kb_service()
    
    # 保存临时文件
    temp_path = f"/tmp/{file.filename}"
    with open(temp_path, "wb") as f:
        f.write(await file.read())
    
    # 上传到知识库
    doc_id = kb.upload_document(
        file_path=temp_path,
        metadata={
            "title": title or file.filename,
            "category": category or "其他",
            "filename": file.filename
        }
    )
    
    return {"doc_id": doc_id, "status": "success"}


@app.delete("/api/v1/kb/documents/{doc_id}")
async def delete_kb_document(doc_id: str):
    """删除文档"""
    kb = get_kb_service()
    success = kb.delete_document(doc_id)
    return {"success": success}


@app.post("/api/v1/kb/search")
async def search_kb(query: str, top_k: int = 5, category: str = None):
    """检索知识库（调试用）"""
    kb = get_kb_service()
    filter_dict = {"metadata.category": category} if category else None
    results = kb.search(query, top_k, filter_dict)
    return {"results": results}
```

---

### 3.5 配置文件

```yaml
# config.yaml
rag:
  enabled: true
  qdrant:
    host: "localhost"
    port: 6333
  
  embedding:
    model: "BAAI/bge-m3"
    device: "cuda"  # 或 "cpu"
  
  reranker:
    model: "BAAI/bge-reranker-v2-m3"
    use_fp16: true
  
  chunking:
    chunk_size: 500
    chunk_overlap: 50
```

---

### 3.6 实施步骤

#### P0: 基础搭建（1-2 天）

**任务清单**:
- [ ] 部署 Qdrant Docker 容器
- [ ] 安装依赖（qdrant-client, unstructured, fastembed, FlagEmbedding）
- [ ] 安装文档解析依赖（poppler-utils, tesseract-ocr）
- [ ] 实现 KB Service 基础类
- [ ] 测试文档上传与向量存储
- [ ] 测试检索功能

**验证标准**:
```bash
# 1. Qdrant 正常运行
curl http://localhost:6333/collections

# 2. 上传测试文档
python test_kb_upload.py

# 3. 检索测试
python test_kb_search.py "未戴安全帽违规"
```

#### P1: Skill 集成（2 天）

**任务清单**:
- [ ] 实现 `kb_regulation_impl` 函数
- [ ] 注册到 Skill Registry
- [ ] 更新 `skills/init.py`
- [ ] 编写单元测试
- [ ] Planner 能发现并调用该 Skill

**验证标准**:
```python
# 1. Registry 包含 kb_regulation
from skills import get_skill_registry
registry = get_skill_registry()
assert "kb_regulation" in [s.id for s in registry.list_skills()]

# 2. 能正常调用
result = await registry.invoke("kb_regulation", {"query": "安全帽规定"})
assert "regulations" in result
```

#### P2: 文档管理接口（1 天）

**任务清单**:
- [ ] FastAPI 路由：上传/删除/检索
- [ ] 文件类型校验（PDF/Word）
- [ ] 元数据管理（title, category, version）
- [ ] Swagger 文档

**验证标准**:
```bash
# 上传文档
curl -X POST http://localhost:8000/api/v1/kb/documents \
  -F "file=@规章制度.pdf" \
  -F "title=安全生产规定" \
  -F "category=安全规定"

# 检索测试
curl -X POST http://localhost:8000/api/v1/kb/search \
  -H "Content-Type: application/json" \
  -d '{"query": "未戴安全帽", "top_k": 5}'
```

#### P3: 端到端测试（2 天）

**任务清单**:
- [ ] 准备测试文档（5-10份规章制度 PDF）
- [ ] 批量上传文档
- [ ] 文本查询测试："未戴安全帽违反哪些规定？"
- [ ] 图片识别 + KB 联动测试（集成 VLM）
- [ ] 验证 Citation 完整性（文本+页码）
- [ ] 性能测试（检索延迟 < 500ms）
- [ ] 准确率评估（人工标注 20 个查询，准确率 > 80%）

**验收标准**:
```bash
# 端到端测试
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test",
    "message": "未戴安全帽违反哪些规定？"
  }'

# 预期响应包含：
# - 具体条文内容
# - 文档标题
# - 页码
# - 相似度评分
```

#### P4: 优化与文档（可选，1 天）

**任务清单**:
- [ ] 调优分块策略（chunk_size, overlap）
- [ ] 调优重排序阈值
- [ ] 添加缓存（Redis）
- [ ] 性能监控（检索耗时、召回率）
- [ ] 更新 DEVELOPER_GUIDE.md（KB 使用说明）
- [ ] 编写运维文档（Qdrant 备份、恢复）

---

### 3.7 部署清单

**依赖安装**:
```bash
# 系统依赖
apt-get update
apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim

# Python 依赖
pip install qdrant-client==1.7.0
pip install langchain-community==0.0.38
pip install unstructured[pdf]==0.12.0
pip install fastembed==0.2.0
pip install FlagEmbedding==1.2.8

# Qdrant 部署
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest
```

**资源需求**:
- **CPU**: 4 核
- **内存**: 8GB（Qdrant 1GB + Embedding 模型 2GB + 应用 2GB + 余量 3GB）
- **磁盘**: 20GB（文档 + 向量存储）
- **GPU**: 可选（加速 Embedding 和 Reranker，推荐 8GB+ 显存）

---

### 3.8 与 RAGFlow/MaxKB 方案对比

| 对比项 | 自建轻量方案 | RAGFlow | MaxKB |
|--------|-------------|---------|-------|
| **资源占用** | < 2GB | 8-10GB | 6-8GB |
| **部署复杂度** | ⭐ Docker 1个 | ⭐⭐⭐⭐ Docker 5个 | ⭐⭐⭐ Docker 3个 |
| **依赖服务** | Qdrant | ES+MySQL+MinIO+Redis | MySQL+ES |
| **功能冗余** | ✅ 无 | ❌ 工作流/UI/Agent | ❌ Agent/对话 |
| **开发工作量** | 3-5天 | 1天（部署） | 1天（部署） |
| **集成方式** | Python SDK | HTTP API | HTTP API |
| **可控性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **文档解析质量** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **扩展性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **运维成本** | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |

**结论**: 对于我们的需求（只需知识库功能，已有 LangGraph 编排），**自建轻量方案是最优选择**。

---

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
