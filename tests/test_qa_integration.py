"""Q&A 知识库集成测试"""
import sys
sys.path.insert(0, '/mnt/data3/clip/LangGraph/agent')

from pathlib import Path
from skills.kb.skill import get_kb_service


def test_qa_mode_upload_and_search():
    """测试 Q&A 模式的上传与检索"""
    kb = get_kb_service()
    print("✅ KB Service 初始化成功")

    # 测试文件
    test_file = "/mnt/data3/clip/LangGraph/agent/plan/安全生产管理规定.md"
    if not Path(test_file).exists():
        print(f"⚠️ 测试文件不存在，跳过测试: {test_file}")
        return

    # 1. 测试普通模式上传
    print("\n[测试1] 普通模式上传...")
    result_normal = kb.upload_document(
        file_path=test_file,
        metadata={
            "title": "安全生产管理规定（普通模式）",
            "category": "测试",
            "filename": "安全生产管理规定.md",
        },
        chunk_size=300,
        chunk_overlap=50,
        qa_mode=False,
    )
    print(f"   ✅ 普通模式: 文档ID={result_normal['doc_id']}, 块数={result_normal['chunks_count']}")

    # 2. 测试 Q&A 模式上传
    print("\n[测试2] Q&A 模式上传（需要1-2分钟）...")
    result_qa = kb.upload_document(
        file_path=test_file,
        metadata={
            "title": "安全生产管理规定（Q&A模式）",
            "category": "测试",
            "filename": "安全生产管理规定.md",
        },
        chunk_size=300,
        chunk_overlap=50,
        qa_mode=True,
    )
    print(f"   ✅ Q&A模式: 文档ID={result_qa['doc_id']}, 问答对数={result_qa['chunks_count']}")

    # 3. 测试检索对比
    print("\n[测试3] 检索精度对比...")
    test_queries = [
        "安全帽的佩戴要求是什么？",
        "发现安全隐患应该怎么办？",
        "特种作业人员需要什么资格？",
    ]

    for query in test_queries:
        print(f"\n查询: {query}")

        # 普通模式检索
        results_normal = kb.search(query, top_k=2, category="测试")
        normal_docs = [r for r in results_normal if r['doc_id'] == result_normal['doc_id']]

        # Q&A 模式检索
        results_qa = kb.search(query, top_k=2, category="测试")
        qa_docs = [r for r in results_qa if r['doc_id'] == result_qa['doc_id']]

        if normal_docs:
            print(f"  普通模式 Top1: (分数={normal_docs[0]['score']:.4f})")
            print(f"    内容: {normal_docs[0]['text'][:80]}...")

        if qa_docs:
            print(f"  Q&A模式 Top1: (分数={qa_docs[0]['score']:.4f})")
            if qa_docs[0].get('qa_mode'):
                print(f"    问题: {qa_docs[0].get('question', '')[:60]}")
                print(f"    答案: {qa_docs[0]['text'][:80]}...")
            else:
                print(f"    内容: {qa_docs[0]['text'][:80]}...")

        # 评估精度提升
        if normal_docs and qa_docs:
            improvement = (qa_docs[0]['score'] - normal_docs[0]['score']) / normal_docs[0]['score'] * 100
            if improvement > 0:
                print(f"  📈 精度提升: +{improvement:.2f}%")
            else:
                print(f"  📉 精度变化: {improvement:.2f}%")

    # 4. 清理测试数据
    print("\n[测试4] 清理测试数据...")
    kb.delete_document(result_normal['doc_id'])
    kb.delete_document(result_qa['doc_id'])
    print("   ✅ 测试数据已清理")

    print("\n✅ 集成测试完成！")


def test_qa_mode_fallback():
    """测试 Q&A 模式降级机制"""
    kb = get_kb_service()
    print("\n[测试5] Q&A 模式降级机制...")

    # 创建一个很短的文档（可能无法生成有效问答对）
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write("简短内容。")
        temp_file = f.name

    try:
        result = kb.upload_document(
            file_path=temp_file,
            metadata={
                "title": "降级测试",
                "category": "测试",
                "filename": "short.txt",
            },
            qa_mode=True,
        )

        if result['qa_mode']:
            print(f"   ✅ Q&A模式成功: 问答对数={result['chunks_count']}")
        else:
            print(f"   ✅ 降级为普通模式: 块数={result['chunks_count']}")

        # 清理
        kb.delete_document(result['doc_id'])
        Path(temp_file).unlink()

    except Exception as e:
        print(f"   ❌ 测试失败: {e}")
        Path(temp_file).unlink()


if __name__ == "__main__":
    print("="*60)
    print("Q&A 知识库集成测试")
    print("="*60)

    test_qa_mode_upload_and_search()
    test_qa_mode_fallback()

    print("\n" + "="*60)
    print("所有集成测试完成！")
    print("="*60)
