"""RAG 知识库包。

对外暴露 KnowledgeBaseService 与 Skill 注册入口。
"""
from .service import KnowledgeBaseService
from .config import KBConfig, get_kb_config

__all__ = ["KnowledgeBaseService", "KBConfig", "get_kb_config"]
