"""OCR 处理器单元测试"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_pdf_to_images():
    """测试 PDF 转图片功能"""
    from skills.kb.ocr_processor import pdf_to_images

    # 使用测试文件
    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"❌ 测试文件不存在: {test_pdf}")
        return False

    try:
        images = asyncio.run(pdf_to_images(str(test_pdf)))
        print(f"✅ PDF 转图片成功: {len(images)} 页")

        # 验证图片有效
        assert len(images) > 0, "图片数量应大于 0"
        for i, img in enumerate(images):
            assert img.size[0] > 0 and img.size[1] > 0, f"第 {i+1} 页图片尺寸无效"

        print(f"   第一页尺寸: {images[0].size}")
        return True
    except Exception as e:
        print(f"❌ PDF 转图片失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ocr_single_page():
    """测试单页 OCR"""
    from skills.kb.ocr_processor import pdf_to_images, ocr_image_to_markdown

    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"❌ 测试文件不存在: {test_pdf}")
        return False

    try:
        # 转换第一页
        images = asyncio.run(pdf_to_images(str(test_pdf)))
        if not images:
            print("❌ 无法获取图片")
            return False

        # OCR 第一页
        markdown = asyncio.run(ocr_image_to_markdown(images[0], 0))
        print(f"✅ 单页 OCR 成功，识别长度: {len(markdown)} 字符")
        print(f"   内容预览: {markdown[:200]}")

        # 验证
        assert len(markdown) > 0, "识别内容不应为空"
        return True
    except Exception as e:
        print(f"❌ 单页 OCR 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_process_full_pdf():
    """测试完整 PDF OCR 处理"""
    from skills.kb.ocr_processor import process_scanned_pdf

    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"❌ 测试文件不存在: {test_pdf}")
        return False

    try:
        markdown = asyncio.run(process_scanned_pdf(str(test_pdf), max_concurrency=2))
        print(f"✅ 完整 PDF OCR 成功，总长度: {len(markdown)} 字符")

        # 验证
        assert len(markdown) > 100, "识别内容长度应大于 100"
        assert "##" in markdown, "应包含页码标记"

        # 保存结果用于人工验收
        output_path = _ROOT / "tests" / "ocr_output_test.md"
        output_path.write_text(markdown, encoding="utf-8")
        print(f"   结果已保存到: {output_path}")

        return True
    except Exception as e:
        print(f"❌ 完整 PDF OCR 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_is_scanned_pdf():
    """测试扫描件检测"""
    from skills.kb.ocr_processor import is_scanned_pdf

    test_pdf = _ROOT / "plan" / "影印版测试文件.pdf"
    if not test_pdf.exists():
        print(f"⚠️  测试文件不存在，跳过")
        return True

    try:
        is_scanned = is_scanned_pdf(str(test_pdf))
        print(f"✅ 扫描件检测: {'是影印版' if is_scanned else '有文本层'}")
        return True
    except Exception as e:
        print(f"❌ 扫描件检测失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("OCR 处理器单元测试")
    print("=" * 60)

    tests = [
        ("PDF 转图片", test_pdf_to_images),
        ("单页 OCR", test_ocr_single_page),
        ("完整 PDF OCR", test_process_full_pdf),
        ("扫描件检测", test_is_scanned_pdf),
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
