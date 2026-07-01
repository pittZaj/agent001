"""Q&A 知识库功能单元测试"""
import sys
import os
sys.path.insert(0, '/mnt/data3/clip/LangGraph/agent')

import asyncio
import pytest
from skills.kb.qa_generator import (
    generate_qa_pairs,
    validate_qa_pair,
    generate_qa_batch,
    semantic_dedup,
)


def test_validate_qa_pair():
    """测试问答对质量校验"""
    source_text = "员工应当遵守劳动纪律，不得迟到早退。迟到超过15分钟视为旷工半天。"

    # 测试 1: 有效的问答对
    valid_qa = {
        "question": "员工迟到超过15分钟会怎样？",
        "answer": "迟到超过15分钟视为旷工半天。"
    }
    assert validate_qa_pair(valid_qa, source_text) == True

    # 测试 2: 没有疑问词的问题
    invalid_qa1 = {
        "question": "员工迟到很严重。",
        "answer": "迟到超过15分钟视为旷工半天。"
    }
    assert validate_qa_pair(invalid_qa1, source_text) == False

    # 测试 3: 问题太短
    invalid_qa2 = {
        "question": "为何",
        "answer": "迟到超过15分钟视为旷工半天。"
    }
    assert validate_qa_pair(invalid_qa2, source_text) == False

    # 测试 4: 答案太短
    invalid_qa3 = {
        "question": "员工迟到会怎样？",
        "answer": "旷工。"
    }
    assert validate_qa_pair(invalid_qa3, source_text) == False

    print("✅ validate_qa_pair 测试通过")


def test_generate_qa_pairs():
    """测试单个块生成问答对"""
    chunk_text = """
    员工请假管理规定：
    1. 病假需提供医院证明
    2. 事假需提前3天申请
    3. 年假需提前15天申请
    """

    result = asyncio.run(generate_qa_pairs(chunk_text, max_pairs=5))

    assert isinstance(result, list)
    assert len(result) >= 1  # 至少生成1个问答对

    for qa in result:
        assert "question" in qa
        assert "answer" in qa
        assert len(qa["question"]) >= 5
        assert len(qa["answer"]) >= 10

    print(f"✅ generate_qa_pairs 测试通过，生成 {len(result)} 个问答对")


def test_generate_qa_batch():
    """测试批量生成问答对"""
    chunks = [
        "员工迟到超过15分钟视为旷工半天。",
        "请假需要提前3天申请，经部门主管批准后生效。",
        "加班需要填写加班申请表，经审批后方可加班。",
    ]

    result = asyncio.run(generate_qa_batch(chunks, max_concurrency=2))

    assert isinstance(result, list)
    assert len(result) >= 1  # 至少生成1个问答对

    for qa in result:
        assert "question" in qa
        assert "answer" in qa
        assert "chunk_idx" in qa
        assert 0 <= qa["chunk_idx"] < len(chunks)

    print(f"✅ generate_qa_batch 测试通过，生成 {len(result)} 个问答对")


def test_semantic_dedup():
    """测试语义去重"""
    qa_list = [
        (0, {"question": "什么是迟到？", "answer": "迟到是指未按时到岗。"}),
        (1, {"question": "如何申请请假？", "answer": "填写请假单。"}),
        (0, {"question": "什么是迟到？", "answer": "迟到是指未按时到岗。"}),  # 重复
        (2, {"question": "加班有补偿吗？", "answer": "有加班费。"}),
    ]

    result = semantic_dedup(qa_list)

    assert len(result) == 3  # 去除1个重复

    # 验证去重后的结构
    for qa in result:
        assert "question" in qa
        assert "answer" in qa
        assert "chunk_idx" in qa

    print(f"✅ semantic_dedup 测试通过，去重后 {len(result)} 个问答对")


if __name__ == "__main__":
    print("开始 Q&A 知识库单元测试...\n")

    test_validate_qa_pair()
    test_generate_qa_pairs()
    test_generate_qa_batch()
    test_semantic_dedup()

    print("\n✅ 所有测试通过！")
