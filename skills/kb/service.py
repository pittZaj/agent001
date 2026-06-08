"""知识库服务核心类。

提供文档上传（解析→分块→向量化→入库）、语义检索（召回→重排序）、
文档管理（列表/删除/统计）功能。

模型说明:
- 词嵌入 BGE-M3：sentence-transformers 从本地路径加载，dim=1024
- 重排序 BGE-reranker-v2-m3：FlagEmbedding 加载
- 均部署在 5880 显卡（agent 环境设备号 cuda:0）
"""
from __future__ import annotations

import os
import uuid

# 离线加载本地模型（服务器无法访问 huggingface.co）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from typing import Any

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from FlagEmbedding import FlagReranker

from .config import KBConfig, get_kb_config

VECTOR_DIM = 1024  # BGE-M3 输出维度


class KnowledgeBaseService:
    """RAG 知识库服务"""

    def __init__(self, config: KBConfig | None = None):
        self.config = config or get_kb_config()
        c = self.config

        logger.info(f"初始化 KB Service: collection={c.collection_name}, device={c.device}")
        self.client = QdrantClient(host=c.qdrant_host, port=c.qdrant_port)
        self.embedding = SentenceTransformer(c.embedding_model_path, device=c.device)
        self.reranker = FlagReranker(c.reranker_model_path, use_fp16=True, devices=c.device)
        self.collection_name = c.collection_name

        self._init_collection()
        logger.info("KB Service 初始化完成")

    def _init_collection(self):
        """初始化 Qdrant collection（幂等）"""
        existing = [col.name for col in self.client.get_collections().collections]
        if self.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info(f"创建 collection: {self.collection_name}")

    # __SPLIT__

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """批量编码文本为向量"""
        vecs = self.embedding.encode(texts, normalize_embeddings=True)
        return vecs.tolist()

    def upload_document(self, file_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """上传文档：解析 → 分块 → 向量化 → 入库

        Args:
            file_path: 文档路径 (PDF/Word/TXT)
            metadata: 元数据 {"title", "category", "filename"}

        Returns:
            {"doc_id": str, "chunks_count": int}
        """
        # 1. 解析
        loader = UnstructuredFileLoader(file_path, mode="single")
        docs = loader.load()
        if not docs or not docs[0].page_content.strip():
            raise ValueError(f"文档解析为空: {file_path}")

        # 2. 分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        texts = [ch.page_content.strip() for ch in chunks if ch.page_content.strip()]
        if not texts:
            raise ValueError(f"分块后无有效内容: {file_path}")

        # 3. 向量化
        vectors = self._embed(texts)

        # 4. 入库
        doc_id = str(uuid.uuid4())
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "text": text,
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "title": metadata.get("title", ""),
                    "category": metadata.get("category", "其他"),
                    "filename": metadata.get("filename", ""),
                },
            )
            for i, (text, vec) in enumerate(zip(texts, vectors))
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info(f"上传文档 doc_id={doc_id}, chunks={len(points)}, title={metadata.get('title')}")
        return {"doc_id": doc_id, "chunks_count": len(points)}

    # __SPLIT2__

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """语义检索 + 重排序

        Args:
            query: 查询文本
            top_k: 最终返回结果数
            category: 可选分类过滤

        Returns:
            检索结果列表（按重排序分数降序）
        """
        # 1. 过滤条件
        query_filter = None
        if category:
            query_filter = Filter(
                must=[FieldCondition(key="category", match=MatchValue(value=category))]
            )

        # 2. 向量召回（top_k * 倍数）
        query_vec = self._embed([query])[0]
        recall = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            limit=top_k * self.config.recall_multiplier,
            query_filter=query_filter,
            with_payload=True,
        ).points
        if not recall:
            return []

        # 3. 重排序
        pairs = [[query, r.payload["text"]] for r in recall]
        scores = self.reranker.compute_score(pairs, normalize=True)
        if isinstance(scores, (int, float)):
            scores = [scores]

        reranked = sorted(zip(recall, scores), key=lambda x: x[1], reverse=True)
        if self.config.score_threshold is not None:
            reranked = [(r, s) for r, s in reranked if s >= self.config.score_threshold]
        reranked = reranked[:top_k]

        return [
            {
                "text": r.payload["text"],
                "score": round(float(score), 4),
                "doc_id": r.payload["doc_id"],
                "chunk_index": r.payload["chunk_index"],
                "title": r.payload.get("title", ""),
                "category": r.payload.get("category", ""),
            }
            for r, score in reranked
        ]

    # __SPLIT3__

    def delete_document(self, doc_id: str) -> int:
        """删除文档的所有分块，返回删除的向量数"""
        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
        count = self.client.count(self.collection_name, count_filter=flt).count
        self.client.delete(collection_name=self.collection_name, points_selector=flt)
        logger.info(f"删除文档 doc_id={doc_id}, chunks={count}")
        return count

    def list_documents(self) -> list[dict[str, Any]]:
        """列出所有文档（按 doc_id 去重，含分块数）"""
        docs: dict[str, dict] = {}
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=256, offset=offset, with_payload=True, with_vectors=False,
            )
            for r in records:
                did = r.payload.get("doc_id")
                if not did:
                    continue
                if did not in docs:
                    docs[did] = {
                        "doc_id": did,
                        "title": r.payload.get("title", ""),
                        "category": r.payload.get("category", ""),
                        "filename": r.payload.get("filename", ""),
                        "chunks_count": 0,
                    }
                docs[did]["chunks_count"] += 1
            if offset is None:
                break
        return list(docs.values())

    def get_stats(self) -> dict[str, Any]:
        """知识库统计信息"""
        info = self.client.get_collection(self.collection_name)
        return {
            "total_vectors": info.points_count,
            "total_documents": len(self.list_documents()),
            "collection_name": self.collection_name,
        }

