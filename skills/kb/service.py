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
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from sentence_transformers import SentenceTransformer
from FlagEmbedding import FlagReranker

from .config import KBConfig, get_kb_config, ChunkStrategy, RetrievalMode

VECTOR_DIM = 1024  # BGE-M3 输出维度


class KnowledgeBaseService:
    """RAG 知识库服务"""

    def __init__(self, config: KBConfig | None = None):
        self.config = config or get_kb_config()
        c = self.config

        logger.info(f"初始化 KB Service: collection={c.collection_name}, device={c.device}")
        self.client = QdrantClient(host=c.qdrant_host, port=c.qdrant_port)
        self.collection_name = c.collection_name
        # 模型延迟加载：仅在真正需要向量化/重排时才占显存。
        # 纯数据库操作（统计/列表/删除/清空/查看）无需加载模型，避免被模型加载问题波及。
        self._embedding = None
        self._reranker = None

        self._init_collection()
        logger.info("KB Service 初始化完成（模型延迟加载）")

    @property
    def embedding(self) -> SentenceTransformer:
        """词嵌入模型（首次访问时加载）"""
        if self._embedding is None:
            c = self.config
            logger.info(f"加载词嵌入模型 BGE-M3: {c.embedding_model_path} → {c.device}")
            self._embedding = SentenceTransformer(c.embedding_model_path, device=c.device)
        return self._embedding

    @property
    def reranker(self) -> FlagReranker:
        """重排序模型（首次访问时加载）"""
        if self._reranker is None:
            c = self.config
            logger.info(f"加载重排序模型 BGE-reranker: {c.reranker_model_path} → {c.device}")
            self._reranker = FlagReranker(c.reranker_model_path, use_fp16=True, devices=c.device)
        return self._reranker

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

    @staticmethod
    def _chunk_fixed_size(text: str, size: int, overlap: int) -> list[str]:
        """固定大小滑动窗口分块，保证相邻块重叠 overlap 字。

        以 step = size - overlap 为步长滑动：块 i 取 text[i*step : i*step+size]，
        因此每相邻两块尾首必然重叠 overlap 字（最后一块除外，可能不足）。
        为减少在词语/数字中间硬切，会在窗口右边界附近就近的标点/换行处微调收尾。
        """
        if size <= 0:
            raise ValueError("分块大小必须 > 0")
        if overlap < 0 or overlap >= size:
            raise ValueError(f"重叠字数必须满足 0 <= overlap < size，当前 size={size}, overlap={overlap}")

        # 归一空白：折叠 3+ 连续换行，避免空块；不破坏正文
        text = text.strip()
        if not text:
            return []

        step = size - overlap
        breakers = "。！？；\n，、 "  # 优先在这些位置收尾
        chunks: list[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + size, n)
            # 若未到文末，尝试把 end 回退到最近的断句点（仅在窗口后 1/4 区间内找，避免块过短）
            if end < n:
                window_floor = start + max(1, (size * 3) // 4)
                cut = -1
                for j in range(end, window_floor, -1):
                    if text[j - 1] in breakers:
                        cut = j
                        break
                if cut != -1:
                    end = cut
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            # 下一块起点 = 本块实际结尾 - overlap（保证重叠），但至少前进 1 防死循环
            start = max(end - overlap, start + 1)
        return chunks

    def upload_document(
        self,
        file_path: str,
        metadata: dict[str, Any],
        chunk_strategy: ChunkStrategy | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        custom_separator: str | None = None,
    ) -> dict[str, Any]:
        """上传文档：解析 → 分块 → 向量化 → 入库

        Args:
            file_path: 文档路径 (PDF/Word/TXT/Markdown)
            metadata: 元数据 {"title", "category", "filename"}
            chunk_strategy: 分块策略，None 则用配置默认
            chunk_size: 固定大小策略的分块字符数，None 则用配置默认
            chunk_overlap: 固定大小策略的重叠字符数，None 则用配置默认
            custom_separator: by_separator 策略的自定义分隔符（如 "****"），None 则不生效

        Returns:
            {"doc_id": str, "chunks_count": int}
        """
        strategy = chunk_strategy or self.config.chunk_strategy
        size = chunk_size if chunk_size is not None else self.config.chunk_size
        overlap = chunk_overlap if chunk_overlap is not None else self.config.chunk_overlap

        # 1. 解析
        loader = UnstructuredFileLoader(file_path, mode="single")
        docs = loader.load()
        if not docs or not docs[0].page_content.strip():
            raise ValueError(f"文档解析为空: {file_path}")

        full_text = docs[0].page_content.strip()

        # 2. 分块（根据策略）
        if strategy == ChunkStrategy.BY_SEPARATOR:
            # 按特殊标记符分割（用户自定义，如 ****）
            if not custom_separator:
                raise ValueError("by_separator 策略需要提供 custom_separator 参数")
            texts = [p.strip() for p in full_text.split(custom_separator) if p.strip()]
        elif strategy == ChunkStrategy.BY_PARAGRAPH:
            # 按段落：双换行符分割
            texts = [p.strip() for p in full_text.split("\n\n") if p.strip()]
        elif strategy == ChunkStrategy.BY_TITLE:
            # 按标题层级（Markdown）：尝试用 MarkdownHeaderTextSplitter
            try:
                md_splitter = MarkdownHeaderTextSplitter(
                    headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")]
                )
                md_docs = md_splitter.split_text(full_text)
                texts = [d.page_content.strip() for d in md_docs if d.page_content.strip()]
            except Exception:
                # 不是 Markdown 或解析失败，降级为段落分割
                texts = [p.strip() for p in full_text.split("\n\n") if p.strip()]
        else:
            # 固定大小（默认）：真正的滑动窗口，保证相邻块重叠 overlap 字。
            # 不用 RecursiveCharacterTextSplitter —— 它先按分隔符切，overlap 仅靠
            # 回并"前序短片段"实现；当段落/句子远长于 overlap 时无法回并，重叠会退化为 0。
            texts = self._chunk_fixed_size(full_text, size, overlap)

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
        extra_info = f"strategy={strategy.value}"
        if strategy == ChunkStrategy.FIXED_SIZE:
            extra_info += f", size={size}, overlap={overlap}"
        elif strategy == ChunkStrategy.BY_SEPARATOR:
            extra_info += f", separator={repr(custom_separator)}"
        logger.info(
            f"上传文档 doc_id={doc_id}, chunks={len(points)}, {extra_info}, title={metadata.get('title')}"
        )
        return {"doc_id": doc_id, "chunks_count": len(points)}


    # __SPLIT2__

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
        retrieval_mode: RetrievalMode | None = None,
        score_threshold: float | None = -1.0,
    ) -> list[dict[str, Any]]:
        """检索 + 重排序

        Args:
            query: 查询文本
            top_k: 最终返回结果数
            category: 可选分类过滤
            retrieval_mode: 检索模式（语义/混合），None 用配置默认
            score_threshold: 重排序最低分；-1.0 表示用配置默认，None 表示不卡阈值

        Returns:
            检索结果列表（按重排序分数降序）
        """
        mode = retrieval_mode or self.config.retrieval_mode
        if score_threshold == -1.0:
            threshold = self.config.score_threshold
        else:
            threshold = score_threshold

        # 1. 过滤条件
        query_filter = None
        if category:
            query_filter = Filter(
                must=[FieldCondition(key="category", match=MatchValue(value=category))]
            )

        # 2. 召回（语义向量）
        recall_n = top_k * self.config.recall_multiplier
        query_vec = self._embed([query])[0]
        recall = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            limit=recall_n,
            query_filter=query_filter,
            with_payload=True,
        ).points
        if not recall:
            return []

        # 2b. 混合检索：补充关键词命中的分块（语义可能漏掉精确词）
        if mode == RetrievalMode.HYBRID:
            recall = self._hybrid_merge(query, recall, query_filter, recall_n)

        # 3. 重排序
        pairs = [[query, r.payload["text"]] for r in recall]
        scores = self.reranker.compute_score(pairs, normalize=True)
        if isinstance(scores, (int, float)):
            scores = [scores]

        reranked = sorted(zip(recall, scores), key=lambda x: x[1], reverse=True)
        if threshold is not None:
            reranked = [(r, s) for r, s in reranked if s >= threshold]
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

    def _hybrid_merge(self, query, recall, query_filter, recall_n):
        """混合检索：在语义召回基础上补充关键词命中的分块。

        用查询中的字/词在全库做 payload 文本匹配，把语义召回漏掉但
        含精确关键词的分块补进候选集，再交给 reranker 统一排序。
        """
        existing_ids = {r.id for r in recall}
        # 提取查询关键词（≥2 字的连续片段，简单分词）
        import re
        terms = [t for t in re.split(r"[\s，。；、？！,.?!]+", query) if len(t) >= 2]
        if not terms:
            return recall

        extra = []
        seen = set(existing_ids)
        # 扫描全库分块，关键词命中则补入（限量，避免过大）
        offset = None
        while len(extra) < recall_n:
            records, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=query_filter,
                limit=256, offset=offset, with_payload=True, with_vectors=False,
            )
            for rec in records:
                if rec.id in seen:
                    continue
                text = rec.payload.get("text", "")
                if any(term in text for term in terms):
                    extra.append(rec)
                    seen.add(rec.id)
                    if len(extra) >= recall_n:
                        break
            if offset is None:
                break
        return list(recall) + extra


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

    def get_document_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        """获取某文档的所有分块内容（用于内容展示/编辑）"""
        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
        chunks = []
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=flt,
                limit=256, offset=offset, with_payload=True, with_vectors=False,
            )
            for r in records:
                chunks.append({
                    "point_id": r.id,
                    "chunk_index": r.payload.get("chunk_index", 0),
                    "text": r.payload.get("text", ""),
                    "title": r.payload.get("title", ""),
                    "category": r.payload.get("category", ""),
                })
            if offset is None:
                break
        chunks.sort(key=lambda x: x["chunk_index"])
        return chunks

    def update_chunk(self, point_id: str, new_text: str) -> bool:
        """修改单个分块内容（重新向量化并更新）"""
        # 取出原 payload
        recs = self.client.retrieve(self.collection_name, ids=[point_id], with_payload=True)
        if not recs:
            raise ValueError(f"分块不存在: {point_id}")
        payload = dict(recs[0].payload)
        payload["text"] = new_text.strip()
        vec = self._embed([new_text.strip()])[0]
        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=point_id, vector=vec, payload=payload)],
        )
        logger.info(f"更新分块 point_id={point_id}")
        return True

    def clear_all(self) -> int:
        """清空整个知识库（删除并重建 collection），返回清空前的向量数"""
        try:
            n = self.client.get_collection(self.collection_name).points_count
        except Exception:
            n = 0
        self.client.delete_collection(self.collection_name)
        self._init_collection()
        logger.info(f"清空知识库 collection={self.collection_name}, 原向量数={n}")
        return n

    def get_stats(self) -> dict[str, Any]:
        """知识库统计信息"""
        info = self.client.get_collection(self.collection_name)
        return {
            "total_vectors": info.points_count,
            "total_documents": len(self.list_documents()),
            "collection_name": self.collection_name,
        }


