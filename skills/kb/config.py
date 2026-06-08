"""知识库配置。

环境约束（详见 agent/plan/RAG_IMPLEMENTATION_PLAN.md）:
- 词嵌入 BGE-M3 / 重排序 BGE-reranker-v2-m3 均从本地路径加载（HF 不可达）
- 全部部署到 5880 显卡；agent 环境中 5880 的设备号即 cuda:0
"""
from __future__ import annotations

from dataclasses import dataclass


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

    # 分块
    chunk_size: int = 300
    chunk_overlap: int = 50

    # 检索
    default_top_k: int = 5
    recall_multiplier: int = 4   # 召回 top_k * 倍数 后重排
    score_threshold: float | None = None  # reranker 输出 logit，默认不卡阈值

    # 上传文件暂存
    upload_dir: str = "/mnt/data3/clip/LangGraph/agent/data/kb_uploads"


def get_kb_config() -> KBConfig:
    """获取知识库配置"""
    return KBConfig()

