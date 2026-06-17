"""RAG 知识库包。

对外暴露 KnowledgeBaseService 与 Skill 注册入口。

注意（2026-06-16）：KnowledgeBaseService 改为延迟暴露。
service.py 顶层 import torch/sentence_transformers/FlagEmbedding（CUDA C 扩展），
若在包导入时就加载，会在 FastAPI(uvicorn) 启动场景与 MCP/uvloop 等库在 C 层冲突
导致段错误。故 `from skills.kb import KnowledgeBaseService` 通过 __getattr__ 懒加载，
轻量的配置类（KBConfig/RetrievalMode 等）仍可直接 import 不受影响。
"""
from .config import KBConfig, get_kb_config, ChunkStrategy, RetrievalMode

__all__ = ["KnowledgeBaseService", "KBConfig", "get_kb_config", "ChunkStrategy", "RetrievalMode"]


def __getattr__(name):
    """PEP 562 模块级懒加载：仅在真正访问 KnowledgeBaseService 时才触发重型 import。"""
    if name == "KnowledgeBaseService":
        from .service import KnowledgeBaseService
        return KnowledgeBaseService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
