"""知识库检索 Skill 注册。

提供全局单例 KB Service 与 kb_regulation Skill，供 LangGraph Planner 动态发现调用。
"""
from __future__ import annotations

from loguru import logger

from skills import Skill, SkillType
from .service import KnowledgeBaseService

# 全局单例（首次调用时才加载模型，避免启动即占显存）
_kb_service: KnowledgeBaseService | None = None


def get_kb_service() -> KnowledgeBaseService:
    """获取 KB Service 单例"""
    global _kb_service
    if _kb_service is None:
        _kb_service = KnowledgeBaseService()
    return _kb_service


def kb_regulation_impl(args: dict, context: dict) -> dict:
    """规章制度检索 Skill 实现"""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "查询内容不能为空", "regulations": [], "total": 0}

    top_k = args.get("top_k", 5)
    category = args.get("category")

    try:
        kb = get_kb_service()
        results = kb.search(query=query, top_k=top_k, category=category)
        return {
            "regulations": [
                {
                    "title": r["title"],
                    "content": r["text"],
                    "score": r["score"],
                    "category": r["category"],
                }
                for r in results
            ],
            "total": len(results),
            "query": query,
        }
    except Exception as e:
        logger.error(f"KB 检索失败: {e}")
        return {"error": f"知识库检索失败: {e}", "regulations": [], "total": 0}


def register_kb_skill(registry):
    """注册知识库 Skill 到 Registry"""
    registry.register(Skill(
        id="kb_regulation",
        name="规章制度检索",
        description="检索安全生产规章制度知识库，返回与查询最相关的条文片段及出处。"
                    "适用于'未戴安全帽违反哪些规定''抽烟处罚标准'等规章查询。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询内容（如：未戴安全帽违反哪些规定）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5,
                },
                "category": {
                    "type": "string",
                    "description": "文档分类过滤（可选）",
                    "enum": ["安全规定", "操作规程", "应急预案", "管理制度"],
                },
            },
            "required": ["query"],
        },
        implementation=kb_regulation_impl,
        skill_type=SkillType.TOOL,
        tags=["knowledge", "regulation", "rag"],
    ))
    logger.info("注册 Skill: kb_regulation")
