# 阶段3：RAG 知识库集成实施文档

> **版本**: v1.1  
> **创建日期**: 2026-06-08  
> **状态**: ✅ 已落地并端到端验证通过  
> **预计工期**: 6-8 天（实际首版当天打通）

---

## 文档说明

本文档是阶段3 RAG知识库集成的**详细可执行实施方案**，包含每个步骤的具体命令、代码示例、验证标准。

> ⚠️ **下方"技术方案概述"及第一/二阶段的部分代码是初版设计。实际落地时因环境约束做了若干调整，请以"实际落地记录"一节为准。**

---

## 实际落地记录（2026-06-08 执行，与初版设计的差异）

落地时因服务器的离线环境、显卡驱动、依赖版本等约束，对初版方案做了如下**关键调整**，这些是实际生效的事实：

### A. 词嵌入：FastEmbed → sentence-transformers
- 初版用 FastEmbed 加载 BGE-M3，但 **FastEmbed 0.8.0 不支持 `BAAI/bge-m3`**（仅支持 bge-small-zh 等）。
- 改为 **sentence-transformers 从本地路径加载 BGE-M3**，dim=1024，已验证。

### B. 模型本地化（服务器无法访问 huggingface.co / pypi.org）
- BGE-M3 词嵌入：`/mnt/data3/clip/LangGraph/VLLM/BGE-M3`（ModelScope 下载，sentence-transformers 格式）
- BGE-reranker-v2-m3：`/mnt/data3/clip/LangGraph/VLLM/bge-reranker-v2-m3`（safetensors 格式，FlagEmbedding 加载）
- 加载模型必须设 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（service.py 已内置）。
- pip 安装走国内镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`。

### C. 显卡与设备号
- **所有 RAG 模型部署到 5880 显卡**（与 vLLM 共用，显存占用小）。
- **agent conda 环境中 5880 的设备号是 `cuda:0`**（服务器显卡设置使然，无需 CUDA_VISIBLE_DEVICES 重映射，与 VLLM 环境的 PCI_BUS_ID 约定不同）。

### D. 依赖版本钉定（关键，否则报错）
- **torch 必须 2.8.0+cu128**：装 FlagEmbedding 会连带升 torch 到 2.12.0+cu130，导致驱动 12.4 下 `cuda.is_available()==False`。已降回 2.8.0 / torchvision 0.23.0 / triton 3.4.0。
- **transformers 必须 `>=4.57,<5.0`**：5.x 删除了 `prepare_for_model`，导致 reranker 报 `XLMRobertaTokenizer has no attribute prepare_for_model`。已钉到 4.57.6。
- 这两条与 VLLM 环境的版本约束完全一致（见 VLLM 部署文档）。

### E. Qdrant API
- qdrant-client 1.18.0 已移除 `.search()`，改用 **`.query_points(...).points`**（service.py 已采用）。

### F. 文档解析与分块
- `UnstructuredFileLoader(mode="single")` + `RecursiveCharacterTextSplitter`。
- **chunk_size 由 500 调小到 300**（条文级检索更精准），chunk_overlap=50。

### G. vLLM 端点变更（与旧部署文档不同）
- 现役 vLLM 是 FP8 量化版：**8004=Qwen3-VL-4B-Instruct-FP8（agent 实际连接）**、8003=Qwen3-VL-8B-Instruct-FP8。
- 不要再用 start_server.sh 起 8002 的 bf16 版（显存不足会 OOM）。

### H. 端到端验证结果 ✅
- 用户问"未戴安全帽违反哪些规定？会被怎么处罚？"
- 主图 Planner 自动拆解为 2 个 kb_regulation 子任务 → Executor 检索 → Formatter 输出带条文和处罚标准（警告/50元/停工整顿、GB2811）的准确回复，无幻觉，error=None。

---

## 技术方案概述（初版设计，部分已被上节覆盖）

### 选型决策

**采用方案**: 自建轻量级 RAG（Qdrant + Unstructured + 词嵌入/重排序）

**技术栈**:
- **向量数据库**: Qdrant（单 Docker 容器，< 1GB 内存）
- **文档解析**: Unstructured（支持复杂 PDF 表格、多栏布局）
- **词嵌入**: BGE-M3（sentence-transformers 从本地加载，dim=1024，GPU）
- **重排序**: BGE-reranker-v2-m3（FlagEmbedding 库，fp16 加速，GPU）

**为什么不用 RAGFlow/MaxKB**:
- ❌ 资源占用 8-10GB（我们只需 < 2GB）
- ❌ 包含不需要的功能（工作流、对话、Agent 管理）
- ❌ 与现有 LangGraph 架构冲突

**关键问题解答**:
1. **是否需要 vLLM 部署词嵌入/重排序模型？**  
   **答：不需要。** 这些是小模型（< 1B 参数），直接在应用进程内用 PyTorch（sentence-transformers / FlagEmbedding）加载到 5880 即可。
   
2. **是否需要知识库上传页面？**  
   **答：是的。** 已集成到现有 Gradio app.py，作为第 7 个 Tab「知识库管理」。

---

## 第一阶段：环境准备（第1天）

### 1.1 部署 Qdrant 向量数据库

```bash
# 创建数据持久化目录
mkdir -p /mnt/data3/clip/LangGraph/agent/qdrant_storage

# 启动 Qdrant Docker 容器
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -p 6334:6334 \
  -v /mnt/data3/clip/LangGraph/agent/qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest

# 验证
curl -s http://localhost:6333/collections | python3 -m json.tool
# 期望输出: {"result": {"collections": []}, "status": "ok", "time": ...}
```

### 1.2 安装系统依赖

```bash
apt-get update
apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim libmagic1
```

**验证**:
```bash
pdftotext -v       # 应输出版本号
tesseract --version # 应输出版本号
```

### 1.3 安装 Python 依赖

```bash
# 激活 agent 环境
source /root/anaconda3/etc/profile.d/conda.sh
conda activate agent

# 安装 RAG 相关依赖
pip install qdrant-client>=1.7.0
pip install "unstructured[pdf]>=0.12.0"
pip install fastembed>=0.2.0
pip install FlagEmbedding>=1.2.8
pip install python-magic>=0.4.27

# 验证安装
python -c "
from qdrant_client import QdrantClient
from fastembed import TextEmbedding
from FlagEmbedding import FlagReranker
print('All RAG dependencies installed successfully')
"
```

### 1.4 下载模型（首次会自动下载到 ~/.cache/）

```bash
python -c "
from fastembed import TextEmbedding
print('Downloading BGE-M3 embedding model...')
model = TextEmbedding('BAAI/bge-m3')
test_vec = list(model.embed(['测试文本']))[0]
print(f'Embedding dim: {len(test_vec)}')  # 期望: 1024
print('BGE-M3 ready!')
"
```

```bash
python -c "
from FlagEmbedding import FlagReranker
print('Downloading BGE-reranker-v2-m3...')
reranker = FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True)
score = reranker.compute_score([['查询', '文档']])
print(f'Reranker score: {score}')
print('Reranker ready!')
"
```

### 1.5 验证 GPU 可用性（可选加速）

```bash
python -c "
import torch
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU count: {torch.cuda.device_count()}')
    for i in range(torch.cuda.device_count()):
        name = torch.cuda.get_device_name(i)
        mem = torch.cuda.get_device_properties(i).total_mem / 1024**3
        print(f'  GPU {i}: {name} ({mem:.1f} GB)')
"
```

> **注意**: 词嵌入/重排序模型可用 CPU 运行（延迟约 200-500ms），如需加速可指定 GPU。
> 当前 5880 已被 vLLM 占用约 40GB，2080 Ti (11GB) 可用于 Embedding + Reranker。

---

## 第二阶段：KB Service 核心开发（第2-3天）

### 2.1 目录结构

```
agent/agent/skills/
├── __init__.py          # 现有
├── base.py              # 现有
├── registry.py          # 现有
├── mcp_skills.py        # 现有
├── alarm_skills.py      # 现有
├── vlm_judge_subgraph.py # 现有
└── kb/                  # 新增
    ├── __init__.py
    ├── service.py       # KB Service 核心类
    ├── config.py        # 配置管理
    └── skill.py         # Skill 注册
```

### 2.2 KB Service 核心实现

**文件**: `agent/agent/skills/kb/service.py`

```python
"""知识库服务核心类。

提供文档上传、语义检索、重排序功能。
"""
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue
)
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from fastembed import TextEmbedding
from FlagEmbedding import FlagReranker

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """RAG 知识库服务"""

    def __init__(
        self,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        collection_name: str = "safety_regulations",
        embedding_model: str = "BAAI/bge-m3",
        reranker_model: str = "BAAI/bge-reranker-v2-m3",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        self.client = QdrantClient(host=qdrant_host, port=qdrant_port)
        self.embedding = TextEmbedding(embedding_model)
        self.reranker = FlagReranker(reranker_model, use_fp16=True)
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self._init_collection()

    def _init_collection(self):
        """初始化 Qdrant collection（幂等）"""
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in collections:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )
            logger.info(f"Created collection: {self.collection_name}")

    def upload_document(self, file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """上传文档到知识库

        Args:
            file_path: 文档路径 (PDF/Word/TXT)
            metadata: 元数据 {"title": str, "category": str, ...}

        Returns:
            {"doc_id": str, "chunks_count": int}
        """
        # 1. 文档解析
        loader = UnstructuredFileLoader(file_path, strategy="hi_res", mode="elements")
        docs = loader.load()

        if not docs:
            raise ValueError(f"文档解析失败，未提取到内容: {file_path}")

        # 2. 文本分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " "],
        )
        chunks = splitter.split_documents(docs)

        # 3. 生成向量
        texts = [chunk.page_content for chunk in chunks]
        vectors = list(self.embedding.embed(texts))

        # 4. 写入 Qdrant
        doc_id = str(uuid.uuid4())
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec.tolist() if hasattr(vec, 'tolist') else list(vec),
                payload={
                    "text": text,
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "title": metadata.get("title", ""),
                    "category": metadata.get("category", "其他"),
                    "filename": metadata.get("filename", ""),
                    "page": getattr(chunks[i], "metadata", {}).get("page_number"),
                },
            )
            for i, (text, vec) in enumerate(zip(texts, vectors))
        ]

        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info(f"Uploaded doc_id={doc_id}, chunks={len(points)}")

        return {"doc_id": doc_id, "chunks_count": len(points)}

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
        score_threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """语义检索 + 重排序

        Args:
            query: 查询文本
            top_k: 最终返回结果数
            category: 可选分类过滤
            score_threshold: 重排序最低分数阈值

        Returns:
            检索结果列表
        """
        # 1. 构建过滤条件
        query_filter = None
        if category:
            query_filter = Filter(
                must=[FieldCondition(key="category", match=MatchValue(value=category))]
            )

        # 2. 向量检索（召回 top_k * 4 条）
        query_vec = list(self.embedding.embed([query]))[0]
        if hasattr(query_vec, 'tolist'):
            query_vec = query_vec.tolist()

        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vec,
            limit=top_k * 4,
            query_filter=query_filter,
        )

        if not results:
            return []

        # 3. 重排序
        docs = [r.payload["text"] for r in results]
        pairs = [[query, doc] for doc in docs]
        scores = self.reranker.compute_score(pairs)

        if isinstance(scores, (int, float)):
            scores = [scores]

        # 4. 排序并过滤
        reranked = sorted(zip(results, scores), key=lambda x: x[1], reverse=True)
        reranked = [(r, s) for r, s in reranked if s >= score_threshold][:top_k]

        return [
            {
                "text": r.payload["text"],
                "score": round(float(score), 4),
                "doc_id": r.payload["doc_id"],
                "chunk_index": r.payload["chunk_index"],
                "title": r.payload.get("title", ""),
                "category": r.payload.get("category", ""),
                "page": r.payload.get("page"),
            }
            for r, score in reranked
        ]

    def delete_document(self, doc_id: str) -> int:
        """删除文档的所有分块

        Returns:
            删除的向量数量
        """
        # 先计数
        count_result = self.client.count(
            collection_name=self.collection_name,
            count_filter=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
        count = count_result.count

        # 删除
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
        logger.info(f"Deleted doc_id={doc_id}, chunks={count}")
        return count

    def list_documents(self) -> List[Dict[str, Any]]:
        """列出所有已上传文档（去重）"""
        # 滚动获取所有 payload
        all_docs = {}
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for r in records:
                doc_id = r.payload.get("doc_id")
                if doc_id and doc_id not in all_docs:
                    all_docs[doc_id] = {
                        "doc_id": doc_id,
                        "title": r.payload.get("title", ""),
                        "category": r.payload.get("category", ""),
                        "filename": r.payload.get("filename", ""),
                    }
            if offset is None:
                break

        # 补充每个文档的 chunk 数
        result = []
        for doc_id, info in all_docs.items():
            count = self.client.count(
                collection_name=self.collection_name,
                count_filter=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
            ).count
            info["chunks_count"] = count
            result.append(info)

        return result

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        collection_info = self.client.get_collection(self.collection_name)
        docs = self.list_documents()
        return {
            "total_vectors": collection_info.points_count,
            "total_documents": len(docs),
            "collection_name": self.collection_name,
        }
```

### 2.3 配置管理

**文件**: `agent/agent/skills/kb/config.py`

```python
"""知识库配置"""
from dataclasses import dataclass, field


@dataclass
class KBConfig:
    """知识库配置"""
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    collection_name: str = "safety_regulations"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    chunk_size: int = 500
    chunk_overlap: int = 50
    default_top_k: int = 5
    score_threshold: float = 0.3
    upload_dir: str = "/mnt/data3/clip/LangGraph/agent/agent/data/kb_uploads"


def get_kb_config() -> KBConfig:
    """获取知识库配置（后续可从 config.yaml 读取）"""
    return KBConfig()
```

### 2.4 Skill 注册

**文件**: `agent/agent/skills/kb/skill.py`

```python
"""知识库检索 Skill 注册"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skills.registry import SkillRegistry

from .service import KnowledgeBaseService
from .config import get_kb_config

logger = logging.getLogger(__name__)

# 全局单例
_kb_service: KnowledgeBaseService | None = None


def get_kb_service() -> KnowledgeBaseService:
    """获取 KB Service 单例"""
    global _kb_service
    if _kb_service is None:
        config = get_kb_config()
        _kb_service = KnowledgeBaseService(
            qdrant_host=config.qdrant_host,
            qdrant_port=config.qdrant_port,
            collection_name=config.collection_name,
            embedding_model=config.embedding_model,
            reranker_model=config.reranker_model,
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
    return _kb_service


async def kb_regulation_impl(args: dict, context: dict) -> dict:
    """规章制度检索 Skill 实现"""
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    category = args.get("category")

    if not query.strip():
        return {"error": "查询内容不能为空"}

    try:
        kb = get_kb_service()
        results = kb.search(query=query, top_k=top_k, category=category)

        return {
            "regulations": [
                {
                    "title": r["title"],
                    "content": r["text"],
                    "page": r["page"],
                    "score": r["score"],
                    "category": r["category"],
                }
                for r in results
            ],
            "total": len(results),
            "query": query,
        }
    except Exception as e:
        logger.error(f"KB search failed: {e}")
        return {"error": f"知识库检索失败: {e}"}


def register_kb_skill(registry):
    """注册知识库 Skill 到 Registry"""
    from skills.base import Skill, SkillType

    skill = Skill(
        id="kb_regulation",
        name="规章制度检索",
        description="检索安全生产规章制度文档，返回相关条文片段及出处",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询内容（如：未戴安全帽违反哪些规定）"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5
                },
                "category": {
                    "type": "string",
                    "description": "文档分类过滤（可选）",
                    "enum": ["安全规定", "操作规程", "应急预案", "管理制度"]
                }
            },
            "required": ["query"]
        },
        implementation=kb_regulation_impl,
        skill_type=SkillType.TOOL,
        tags=["knowledge", "regulation", "rag"],
    )
    registry.register(skill)
    logger.info("Registered skill: kb_regulation")
```

---

## 第三阶段：Web 页面集成（第4天）

### 3.1 新增知识库管理 Tab

在现有 Gradio app.py 中新增第 7 个 Tab "知识库管理"。

**新增文件**: `agent/agent/web/kb_manager.py`

功能：
1. **文档上传** — 支持 PDF/Word/TXT，填写标题和分类
2. **文档列表** — 已上传文档概览，支持删除
3. **检索测试** — 输入查询词预览检索结果
4. **统计信息** — 文档总数、向量总数

### 3.2 集成到 app.py

在 `agent/agent/web/app.py` 中添加：

```python
# 在 import 区域添加:
from web.kb_manager import build_kb_tab

# 在 Gradio Blocks 构建中最后一个 Tab 后添加:
build_kb_tab()
```

---

## 第四阶段：Skill Registry 集成（第5天）

### 4.1 更新 skills/__init__.py

```python
# 在现有的 skill 注册逻辑后添加:
from skills.kb.skill import register_kb_skill
register_kb_skill(registry)
```

### 4.2 验证 Planner 自动发现

```python
from skills import get_skill_registry
registry = get_skill_registry()
skills = registry.list_skills()
kb_skill = [s for s in skills if s.id == "kb_regulation"]
assert len(kb_skill) == 1
print(f"✅ kb_regulation 已注册: {kb_skill[0].description}")
```

---

## 第五阶段：端到端测试与验证（第6-7天）

### 5.1 准备测试文档

```bash
mkdir -p /mnt/data3/clip/LangGraph/agent/agent/data/kb_test_docs
# 将规章制度 PDF 放入该目录（至少 3-5 份）
```

### 5.2 性能基准

- 检索延迟目标: < 500ms（含重排序）
- 准确率目标: > 80%（人工评估 20 个查询）

### 5.3 VLM + KB 联动测试

```
VLM 识别: "未戴安全帽" → 自动触发 kb_regulation("未戴安全帽违反哪些规定？")
→ 返回具体条文 + 出处 + 页码
```

---

## 验收标准

| # | 检查项 | 标准 | 验证方式 |
|---|--------|------|----------|
| 1 | Qdrant 运行正常 | HTTP 200 | `curl localhost:6333/collections` |
| 2 | 文档上传成功 | 返回 doc_id | 上传 PDF 验证 |
| 3 | 语义检索有效 | top-5 含相关结果 | 测试 5 个查询 |
| 4 | 重排序工作 | score 递减排序 | 检查分数 |
| 5 | Skill 已注册 | registry 可发现 | `list_skills()` |
| 6 | Web 页面正常 | 上传/检索/删除可用 | Gradio 操作 |
| 7 | 检索延迟 | < 500ms | 性能测试 |
| 8 | VLM 联动 | 识别→查规章 | 联动测试 |

---

## 新增文件清单

| 文件路径 | 操作 | 说明 |
|---------|------|------|
| `agent/skills/kb/__init__.py` | 新增 | 包初始化 |
| `agent/skills/kb/service.py` | 新增 | KB Service 核心 |
| `agent/skills/kb/config.py` | 新增 | 配置管理 |
| `agent/skills/kb/skill.py` | 新增 | Skill 注册 |
| `agent/web/kb_manager.py` | 新增 | Gradio 知识库 Tab |
| `agent/web/app.py` | 修改 | 添加第 7 Tab |
| `agent/skills/__init__.py` | 修改 | 注册 kb_regulation |
| `tests/test_kb/` | 新增 | 测试套件 |

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| PDF 解析失败（扫描件） | 启用 OCR (tesseract) |
| Embedding 模型下载慢 | 提前下载或使用 HF 镜像 |
| GPU 显存不足 | 默认 CPU 运行，延迟可接受 |
| Qdrant 异常 | 数据挂载本地卷，重启即恢复 |

---

**文档维护**: 随实施进展持续更新  
**最后更新**: 2026-06-08
