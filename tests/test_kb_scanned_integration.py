"""知识库影印版 PDF 集成测试（端到端）"""
import sys
from pathlib import Path

# 添加项目根目录到路径
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_upload_scanned_pdf():
    """测试上传影印版 PDF 到知识库"""
    from skills.kb.service import KnowledgeBaseService
    from skills.kb import ChunkStrategy

    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"❌ 测试文件不存在: {test_pdf}")
        return False

    try:
        kb = KnowledgeBaseService()

        # 上传影印版 PDF
        print("📤 开始上传影印版 PDF...")
        result = kb.upload_document(
            file_path=str(test_pdf),
            metadata={
                "title": "影印版测试文档",
                "category": "测试",
                "filename": "影印版测试文件.pdf",
            },
            chunk_strategy=ChunkStrategy.FIXED_SIZE,
            chunk_size=300,
            chunk_overlap=50,
            is_scanned=True,  # 标记为影印版
            qa_mode=False,
        )

        print(f"✅ 上传成功:")
        print(f"   文档ID: {result['doc_id']}")
        print(f"   分块数: {result['chunks_count']}")
        print(f"   影印版: {result['is_scanned']}")

        # 验证
        assert result['chunks_count'] > 0, "分块数应大于 0"
        assert result['is_scanned'] is True, "应标记为影印版"

        # 测试检索
        doc_id = result['doc_id']
        return test_search_scanned_content(kb, doc_id)

    except Exception as e:
        print(f"❌ 上传失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_search_scanned_content(kb, doc_id):
    """测试检索影印版文档内容"""
    try:
        print("\n🔍 测试检索影印版内容...")

        # 测试查询
        queries = [
            "心血管疾病",
            "心律失常",
            "深度学习",
        ]

        for query in queries:
            print(f"\n   查询: {query}")
            results = kb.search(query, top_k=3)

            if results:
                print(f"   ✅ 找到 {len(results)} 条结果")
                for i, r in enumerate(results[:2], 1):
                    print(f"      {i}. 相关度: {r['score']:.4f}")
                    print(f"         内容: {r['text'][:80]}...")
            else:
                print(f"   ⚠️  未找到结果")

        print("\n✅ 检索测试完成")
        return True

    except Exception as e:
        print(f"❌ 检索失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_upload_scanned_with_qa_mode():
    """测试影印版 PDF + Q&A 模式"""
    from skills.kb.service import KnowledgeBaseService
    from skills.kb import ChunkStrategy

    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"❌ 测试文件不存在: {test_pdf}")
        return False

    try:
        kb = KnowledgeBaseService()

        # 上传影印版 PDF + Q&A 模式
        print("📤 开始上传影印版 PDF (Q&A 模式)...")
        result = kb.upload_document(
            file_path=str(test_pdf),
            metadata={
                "title": "影印版测试文档(Q&A)",
                "category": "测试",
                "filename": "影印版测试文件_qa.pdf",
            },
            chunk_strategy=ChunkStrategy.FIXED_SIZE,
            chunk_size=500,
            chunk_overlap=50,
            is_scanned=True,  # 标记为影印版
            qa_mode=True,     # 启用 Q&A 模式
        )

        print(f"✅ 上传成功:")
        print(f"   文档ID: {result['doc_id']}")
        print(f"   分块数: {result['chunks_count']}")
        print(f"   影印版: {result['is_scanned']}")
        print(f"   Q&A 模式: {result['qa_mode']}")

        # 验证
        assert result['chunks_count'] > 0, "分块数应大于 0"
        assert result['is_scanned'] is True, "应标记为影印版"
        assert result['qa_mode'] is True, "应标记为 Q&A 模式"

        # 测试 Q&A 检索
        print("\n🔍 测试 Q&A 模式检索...")
        results = kb.search("什么是心血管疾病？", top_k=3)

        if results:
            print(f"✅ 找到 {len(results)} 条结果")
            for i, r in enumerate(results[:2], 1):
                if r.get('qa_mode'):
                    print(f"   {i}. [Q&A] 相关度: {r['score']:.4f}")
                    print(f"      问题: {r.get('question', 'N/A')[:60]}...")
                    print(f"      答案: {r['text'][:80]}...")
                else:
                    print(f"   {i}. [普通] 相关度: {r['score']:.4f}")
                    print(f"      内容: {r['text'][:80]}...")
        else:
            print("⚠️  未找到结果")

        return True

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有集成测试"""
    print("=" * 60)
    print("知识库影印版 PDF 集成测试")
    print("=" * 60)

    tests = [
        ("上传影印版 PDF", test_upload_scanned_pdf),
        ("影印版 + Q&A 模式", test_upload_scanned_with_qa_mode),
    ]

    results = []
    for name, test_func in tests:
        print(f"\n[测试] {name}")
        print("-" * 60)
        result = test_func()
        results.append((name, result))

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {name}")

    print(f"\n总计: {passed}/{total} 通过")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
