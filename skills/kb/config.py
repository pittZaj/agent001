"""知识库配置。

环境约束（详见 agent/plan/RAG_IMPLEMENTATION_PLAN.md）:
- 词嵌入 BGE-M3 / 重排序 BGE-reranker-v2-m3 均从本地路径加载（HF 不可达）
- 全部部署到 5880 显卡；agent 环境中 5880 的设备号即 cuda:0
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChunkStrategy(str, Enum):
    """分块策略"""
    FIXED_SIZE = "fixed_size"       # 固定字符数（默认）
    BY_PARAGRAPH = "by_paragraph"   # 按段落（\n\n 分割）
    BY_TITLE = "by_title"           # 按标题层级（Markdown #）


class RetrievalMode(str, Enum):
    """检索模式"""
    SEMANTIC = "semantic"           # 纯语义向量检索
    HYBRID = "hybrid"               # 混合检索（语义+BM25关键词，效果最好）


@dataclass
class KBConfig:
    """知识库配置"""
    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    collection_name: str = "safety_regulations"

    # 模型本地路径
    embedding_model_path: str = "/mnt/data3/clip/LangGraph/VLLM/BGE-M3"
    reranker_model_path: str = "/mnt/data3/clip/LangGraph/VLLM/bge-reranker-v2-m3"

    # 设备：agent 环境中 5880 显卡的设备号为 cuda:0
    device: str = "cuda:0"

    # 分块策略与参数
    chunk_strategy: ChunkStrategy = ChunkStrategy.FIXED_SIZE
    chunk_size: int = 300            # 固定大小策略的字符数
    chunk_overlap: int = 50          # 重叠字符数

    # 检索模式与参数
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID  # 混合检索效果最好
    default_top_k: int = 5           # 默认召回数量
    recall_multiplier: int = 4       # 召回 top_k * 倍数 后重排
    score_threshold: float | None = 0.5  # 重排序最低分（0.5-0.7，低了不相关内容会出现）

    # 上传文件暂存
    upload_dir: str = "/mnt/data3/clip/LangGraph/agent/data/kb_uploads"


def get_kb_config() -> KBConfig:
    """获取知识库配置"""
    return KBConfig()


