"""问答对生成器（基于 vLLM 8004）

用于知识库 Q&A 模式，将文档分块转换为问答对，提升检索精度。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import List, Dict

from loguru import logger

# Q&A 生成提示词
QA_GEN_PROMPT = """请根据以下文本生成 3~5 个高质量问答对。

要求：
1. 问题类型多样：定义解释、操作步骤、数值信息、条件判断
2. 问题必须是完整疑问句，包含疑问词（什么、如何、哪些、是否、多少等）
3. 答案直接来自文本内容，忠实原文，不编造
4. 每个问答对独立完整，不依赖上下文
5. 严格按 JSON 数组格式输出：[{{"question":"...","answer":"..."}}]

文本内容：
{text}

请输出 JSON 数组："""


async def generate_qa_pairs(chunk_text: str, max_pairs: int = 5) -> List[Dict[str, str]]:
    """为单个文本块生成问答对

    Args:
        chunk_text: 文本块内容
        max_pairs: 最多生成问答对数量

    Returns:
        问答对列表 [{"question": "...", "answer": "..."}, ...]
    """
    try:
        from utils.llm_pool import get_llm

        llm = get_llm(role="qa_generator", temperature=0.3)  # 低温保证质量
        prompt = QA_GEN_PROMPT.format(text=chunk_text[:2000])  # 限制输入长度

        # 调用 vLLM（同步转异步）
        response = await asyncio.to_thread(llm.invoke, prompt)
        content = response.content.strip()

        # 解析 JSON
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if not json_match:
            logger.warning(f"[QA Gen] 未找到 JSON 数组: {content[:100]}")
            return []

        qa_list = json.loads(json_match.group())

        # 质量校验
        valid_qa = []
        for item in qa_list[:max_pairs]:
            if validate_qa_pair(item, chunk_text):
                valid_qa.append(item)

        logger.info(f"[QA Gen] 生成 {len(valid_qa)} 个有效问答对（原始 {len(qa_list)} 个）")
        return valid_qa

    except Exception as e:
        logger.error(f"[QA Gen] 生成失败: {e}")
        return []


def validate_qa_pair(qa: Dict, source_text: str) -> bool:
    """质量校验（继承 Q_A.py 思想但放宽规则）

    Args:
        qa: 问答对字典 {"question": "...", "answer": "..."}
        source_text: 原始文本块

    Returns:
        是否通过校验
    """
    q = qa.get("question", "").strip()
    a = qa.get("answer", "").strip()

    # 1. 问题必须含疑问词
    question_words = ['什么', '如何', '哪些', '是否', '怎样', '为什么', '多少', '几个', '何时', '何地']
    if not any(word in q for word in question_words):
        return False

    # 2. 问题长度限制
    if not (5 <= len(q) <= 100):  # 放宽到100字
        return False

    # 3. 答案长度限制
    if not (10 <= len(a) <= 500):
        return False

    # 4. 答案语义忠实性校验（放宽版：关键词重叠）
    try:
        import jieba
        answer_words = set(jieba.lcut(a))
        source_words = set(jieba.lcut(source_text))
        overlap = len(answer_words & source_words) / max(len(answer_words), 1)
        if overlap < 0.3:  # 至少 30% 词汇重叠
            return False
    except Exception as e:
        logger.warning(f"[QA Validate] jieba 分词失败: {e}，跳过重叠检查")

    return True


async def generate_qa_batch(chunks: List[str], max_concurrency: int = 3) -> List[Dict]:
    """批量生成问答对（并发控制）

    Args:
        chunks: 文本块列表
        max_concurrency: 最大并发数

    Returns:
        问答对列表 [{"chunk_idx": 0, "question": "...", "answer": "..."}, ...]
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_with_limit(chunk_text, chunk_idx):
        async with semaphore:
            qa_pairs = await generate_qa_pairs(chunk_text)
            return [(chunk_idx, qa) for qa in qa_pairs]

    logger.info(f"[QA Batch] 开始批量生成，共 {len(chunks)} 个块，并发数 {max_concurrency}")
    tasks = [_gen_with_limit(chunk, idx) for idx, chunk in enumerate(chunks)]
    results = await asyncio.gather(*tasks)

    # 扁平化 + 去重
    all_qa = [qa for sublist in results for qa in sublist]
    unique_qa = semantic_dedup(all_qa)

    logger.info(f"[QA Batch] 完成，生成 {len(unique_qa)} 个问答对（去重前 {len(all_qa)} 个）")
    return unique_qa


def semantic_dedup(qa_list: List[tuple], threshold: float = 0.95) -> List[Dict]:
    """语义去重（问题向量相似度 > 0.95 视为重复）

    简化版：仅做字面去重（精确匹配）
    完整版可用 embedding 模型计算相似度

    Args:
        qa_list: [(chunk_idx, qa), ...]
        threshold: 相似度阈值（当前版本未使用）

    Returns:
        去重后的问答对列表
    """
    if not qa_list:
        return []

    # 简化版：仅做字面去重
    seen = set()
    unique = []
    for chunk_idx, qa in qa_list:
        key = (qa['question'], qa['answer'])
        if key not in seen:
            seen.add(key)
            unique.append({"chunk_idx": chunk_idx, **qa})

    return unique
