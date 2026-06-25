"""知识库源文档归档（永久保留原始上传文件）。"""
from __future__ import annotations

import os
import shutil
from urllib.parse import quote


def archive_source(source_root: str, doc_id: str, src_path: str, filename: str) -> tuple[str, int]:
    """复制源文件到 source_root/{doc_id}/，返回 (绝对路径, 字节大小)。"""
    os.makedirs(source_root, exist_ok=True)
    doc_dir = os.path.join(source_root, doc_id)
    os.makedirs(doc_dir, exist_ok=True)
    safe_name = os.path.basename(filename or os.path.basename(src_path)) or "document"
    dest = os.path.join(doc_dir, safe_name)
    shutil.copy2(src_path, dest)
    return dest, os.path.getsize(dest)


def remove_source(source_path: str | None) -> None:
    if not source_path or not os.path.isfile(source_path):
        return
    doc_dir = os.path.dirname(source_path)
    try:
        os.remove(source_path)
    except OSError:
        pass
    try:
        if os.path.isdir(doc_dir) and not os.listdir(doc_dir):
            os.rmdir(doc_dir)
    except OSError:
        pass


def remove_source_by_doc_id(source_root: str, doc_id: str) -> None:
    """按 doc_id 目录删除源文件（MySQL 无记录时的兜底）。"""
    if not source_root or not doc_id:
        return
    doc_dir = os.path.join(source_root, doc_id)
    if os.path.isdir(doc_dir):
        shutil.rmtree(doc_dir, ignore_errors=True)


def remove_source_tree(source_root: str) -> None:
    if not source_root or not os.path.isdir(source_root):
        return
    shutil.rmtree(source_root, ignore_errors=True)
    os.makedirs(source_root, exist_ok=True)


def resolve_source_file_path(doc_id: str) -> str | None:
    """解析源文件路径（仅 MySQL + 磁盘，不依赖 Qdrant/模型）。"""
    if not doc_id:
        return None
    from .config import get_kb_config
    from .mysql_store import get_mysql_store

    store = get_mysql_store()
    if store:
        doc = store.get_document(doc_id)
        if doc:
            path = (doc.get("source_file_path") or "").strip()
            if path and os.path.isfile(path):
                return path

    cfg = get_kb_config()
    doc_dir = os.path.join(cfg.source_dir, doc_id)
    if os.path.isdir(doc_dir):
        for name in sorted(os.listdir(doc_dir)):
            full = os.path.join(doc_dir, name)
            if os.path.isfile(full):
                return full
    return None


def content_disposition_header(disposition: str, filename: str) -> str:
    """生成兼容中文文件名的 Content-Disposition（避免 latin-1 编码 500）。"""
    name = os.path.basename(filename or "") or "document"
    ascii_fallback = name.encode("ascii", "ignore").decode().strip() or "document"
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(name)}'
