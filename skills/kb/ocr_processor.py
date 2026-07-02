"""影印版 PDF OCR 处理器（基于 Qwen3-VL-4B）

将扫描件 PDF 转换为 Markdown 文本，供知识库入库。
流程：PDF → 图片（PyMuPDF）→ OCR（Qwen3-VL-4B）→ Markdown
"""
from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path
from typing import List

import fitz  # PyMuPDF
from PIL import Image
from loguru import logger


# OCR 提示词
OCR_PROMPT = """请提取这张图片中的所有文字内容，输出为 Markdown 格式。

要求：
1. 保留标题层级（用 #、##、### 标记）
2. 表格用 Markdown 表格语法
3. 列表用 - 或 1. 标记
4. 忠实原图文字，不添加解释
5. 如果图片中无文字，返回空字符串

请开始提取："""


async def pdf_to_images(pdf_path: str, dpi: int = 144) -> List[Image.Image]:
    """将 PDF 转换为高质量图片列表

    Args:
        pdf_path: PDF 文件路径
        dpi: 分辨率（默认 144，平衡清晰度与速度）

    Returns:
        PIL Image 列表
    """
    images = []
    try:
        pdf_document = fitz.open(pdf_path)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)

            # 转 PIL Image
            img_data = pixmap.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            images.append(img)

        pdf_document.close()
        logger.info(f"[OCR] PDF 转图片成功: {len(images)} 页")
        return images
    except Exception as e:
        logger.error(f"[OCR] PDF 转图片失败: {e}")
        raise


async def ocr_image_to_markdown(image: Image.Image, page_idx: int) -> str:
    """对单张图片进行 OCR（调用 Qwen3-VL-4B）

    Args:
        image: PIL Image 对象
        page_idx: 页码索引（从 0 开始）

    Returns:
        Markdown 格式的提取文本
    """
    try:
        # 保存临时图片
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir) / f"page_{page_idx}.jpg"
        image.save(temp_path, format="JPEG", quality=95)

        # 调用 VLM（复用现有 utils/vlm.py 的客户端）
        from utils.vlm import get_vlm_client
        import base64

        vlm = get_vlm_client()

        # 读取图片为 base64
        with open(temp_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # 构造消息（与 judge_alarm_type 类似的结构）
        image_url = f"data:image/jpeg;base64,{img_b64}"
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }]

        # 异步调用（用 asyncio.to_thread 包装同步调用）
        response = await asyncio.to_thread(vlm.client.invoke, messages)
        markdown_text = response.content.strip()

        # 清理临时文件
        temp_path.unlink()
        Path(temp_dir).rmdir()

        logger.info(f"[OCR] 第 {page_idx+1} 页提取完成，长度: {len(markdown_text)}")
        return markdown_text

    except Exception as e:
        logger.error(f"[OCR] 第 {page_idx+1} 页处理失败: {e}")
        return f"<!-- 第 {page_idx+1} 页 OCR 失败: {e} -->\n"


async def process_scanned_pdf(
    pdf_path: str,
    max_concurrency: int = 2,
    min_text_length: int = 50,
) -> str:
    """处理影印版 PDF：PDF → 图片 → OCR → Markdown

    Args:
        pdf_path: PDF 文件路径
        max_concurrency: 最大并发 OCR 数（避免打爆 8004）
        min_text_length: 最小文本长度（低于此长度视为识别失败，跳过该页）

    Returns:
        完整的 Markdown 文本
    """
    logger.info(f"[OCR] 开始处理影印版 PDF: {pdf_path}")

    # 第一阶段：PDF → 图片
    images = await pdf_to_images(pdf_path)
    if not images:
        raise ValueError("PDF 转图片失败，无法处理影印版文件")

    # 第二阶段：并发 OCR（限流避免打爆 8004）
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _ocr_with_limit(img, idx):
        async with semaphore:
            return await ocr_image_to_markdown(img, idx)

    tasks = [_ocr_with_limit(img, idx) for idx, img in enumerate(images)]
    page_markdowns = await asyncio.gather(*tasks)

    # 第三阶段：合并 Markdown（过滤识别失败的页面）
    valid_pages = []
    for i, md in enumerate(page_markdowns):
        # 跳过识别失败或内容过短的页面
        clean_md = md.replace("<!--", "").replace("-->", "").strip()
        if clean_md and len(clean_md) >= min_text_length and not clean_md.startswith("第") and "OCR 失败" not in md:
            valid_pages.append((i, md))
        else:
            logger.warning(f"[OCR] 第 {i+1} 页识别质量低（长度 {len(clean_md)}），跳过")

    if not valid_pages:
        raise ValueError(f"OCR 识别失败：{len(images)} 页中无有效内容（可能是图片质量过低或非文本内容）")

    full_markdown = "\n\n---\n\n".join([
        f"## 第 {i+1} 页\n\n{md}"
        for i, md in valid_pages
    ])

    logger.info(f"[OCR] 处理完成，有效页数: {len(valid_pages)}/{len(images)}，总长度: {len(full_markdown)} 字符")
    return full_markdown


def is_scanned_pdf(pdf_path: str) -> bool:
    """检测 PDF 是否为扫描件（无文本层）

    Args:
        pdf_path: PDF 文件路径

    Returns:
        True 表示是扫描件，False 表示有文本层
    """
    try:
        doc = fitz.open(pdf_path)
        # 检查前 3 页是否有文本（扫描件通常全页无文本）
        has_text = False
        check_pages = min(3, doc.page_count)

        for page_num in range(check_pages):
            page = doc[page_num]
            text = page.get_text().strip()
            if text and len(text) > 50:  # 超过 50 字符认为有文本
                has_text = True
                break

        doc.close()
        return not has_text
    except Exception as e:
        logger.warning(f"[OCR] 检测 PDF 类型失败: {e}，默认按普通 PDF 处理")
        return False
