"""知识库 HTTP API（兼容 KSIPMS Go kb_http_client 路径 /kb/*）。"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from skills.kb import ChunkStrategy, RetrievalMode
from skills.kb.mysql_store import get_mysql_store, load_mysql_config
from skills.kb.skill import get_kb_service
from skills.kb.source_files import content_disposition_header, resolve_source_file_path
from utils import CONFIG_PATH

router = APIRouter(tags=["knowledge-base"])
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb_api")


async def _run_sync(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


def _ok(data: Any = None) -> dict[str, Any]:
    return {"data": data}


def _err(msg: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=msg)


class KbSearchBody(BaseModel):
    query: str
    top_k: int = 5
    category: str | None = None
    retrieval_mode: str = "hybrid"
    score_threshold: float | None = None


class KbUpdateChunkBody(BaseModel):
    text: str


class KbGroupCreateBody(BaseModel):
    name: str
    description: str = ""
    sort_order: int = 0


class KbGroupUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


class KbUpdateDocumentBody(BaseModel):
    group_id: int | None = None


@router.get("/healthz")
async def kb_healthz():
    try:
        kb = get_kb_service()
        qdrant = {
            "qdrant_host": kb.config.qdrant_host,
            "qdrant_port": kb.config.qdrant_port,
            "collection_name": kb.collection_name,
            "collection_ready": kb.collection_name in [
                c.name for c in kb.client.get_collections().collections
            ],
        }
        store = get_mysql_store()
        mysql_ok = False
        if store:
            await _run_sync(store.ensure_schema)
            mysql_ok = True
        return {"ok": True, "data": {**qdrant, "mysql_enabled": mysql_ok}}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/kb/stats")
async def kb_stats():
    try:
        data = await _run_sync(get_kb_service().get_stats)
        return _ok(data)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.get("/kb/documents")
async def kb_list_documents(group_id: int | None = Query(default=None)):
    try:
        data = await _run_sync(get_kb_service().list_documents, group_id)
        return _ok(data)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.post("/kb/upload")
async def kb_upload(request: Request):
    """上传文档：支持 multipart（Go OM 代理）或 JSON file_path（同机直调）。"""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise _err("file required")
        import uuid as _uuid

        cfg = get_kb_service().config
        os.makedirs(cfg.upload_dir, exist_ok=True)
        orig_name = getattr(upload, "filename", None) or "upload.bin"
        dest = os.path.join(cfg.upload_dir, f"{_uuid.uuid4().hex}_{orig_name}")
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        file_path = dest
        title = str(form.get("title") or "").strip()
        category = str(form.get("category") or "其他").strip() or "其他"
        filename = str(form.get("filename") or orig_name).strip() or orig_name
        chunk_strategy = str(form.get("chunk_strategy") or "fixed_size")
        chunk_size = int(form.get("chunk_size") or 300)
        chunk_overlap = int(form.get("chunk_overlap") or 50)
        custom_separator = str(form.get("custom_separator") or "").strip() or None
        group_id_raw = form.get("group_id")
        group_id = None
        if group_id_raw is not None and str(group_id_raw).strip() != "":
            try:
                group_id = int(group_id_raw)
            except (TypeError, ValueError) as exc:
                raise _err("invalid group_id") from exc
    else:
        try:
            body = await request.json()
        except Exception as exc:
            raise _err("invalid json") from exc
        file_path = str(body.get("file_path") or "").strip()
        if not file_path or not os.path.isfile(file_path):
            raise _err(f"文件不存在或不可访问: {file_path}")
        title = str(body.get("title") or "").strip()
        category = str(body.get("category") or "其他").strip() or "其他"
        filename = str(body.get("filename") or "").strip() or os.path.basename(file_path)
        chunk_strategy = str(body.get("chunk_strategy") or "fixed_size")
        chunk_size = int(body.get("chunk_size", 300))
        chunk_overlap = int(body.get("chunk_overlap", 50))
        custom_separator = str(body.get("custom_separator") or "").strip() or None
        group_id = body.get("group_id")
        if group_id is not None:
            try:
                group_id = int(group_id)
            except (TypeError, ValueError) as exc:
                raise _err("invalid group_id") from exc

    try:
        strategy = ChunkStrategy(chunk_strategy)
    except ValueError as exc:
        raise _err(f"invalid chunk_strategy: {chunk_strategy}") from exc
    metadata = {
        "title": title or os.path.splitext(os.path.basename(file_path))[0],
        "category": category,
        "filename": filename or os.path.basename(file_path),
        "group_id": group_id,
    }
    kwargs: dict[str, Any] = {}
    if strategy == ChunkStrategy.FIXED_SIZE:
        kwargs["chunk_size"] = chunk_size
        kwargs["chunk_overlap"] = chunk_overlap
    elif strategy == ChunkStrategy.BY_SEPARATOR:
        sep = (custom_separator or "").strip()
        if not sep:
            raise _err("custom_separator required for by_separator")
        kwargs["custom_separator"] = sep
    try:
        result = await _run_sync(
            get_kb_service().upload_document,
            file_path,
            metadata,
            strategy,
            kwargs.get("chunk_size"),
            kwargs.get("chunk_overlap"),
            kwargs.get("custom_separator"),
        )
        return _ok(result)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.post("/kb/search")
async def kb_search(body: KbSearchBody):
    if not body.query.strip():
        raise _err("query required")
    try:
        mode = RetrievalMode(body.retrieval_mode)
    except ValueError as exc:
        raise _err(f"invalid retrieval_mode: {body.retrieval_mode}") from exc
    thresh = body.score_threshold if body.score_threshold is not None else -1.0
    try:
        data = await _run_sync(
            get_kb_service().search,
            body.query.strip(),
            body.top_k,
            (body.category or "").strip() or None,
            mode,
            thresh,
        )
        return _ok(data)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.get("/kb/documents/{doc_id}")
async def kb_get_document(doc_id: str):
    try:
        data = await _run_sync(get_kb_service().get_document, doc_id)
        if not data:
            raise _err("document not found", 404)
        return _ok(data)
    except HTTPException:
        raise
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.put("/kb/documents/{doc_id}")
async def kb_update_document(doc_id: str, body: KbUpdateDocumentBody):
    try:
        data = await _run_sync(
            get_kb_service().update_document_group,
            doc_id,
            body.group_id,
        )
        return _ok(data)
    except ValueError as exc:
        raise _err(str(exc), 400) from exc
    except Exception as exc:
        raise _err(str(exc), 500) from exc


def _source_media_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _can_inline_preview(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in {".txt", ".md", ".markdown", ".pdf"}


@router.get("/kb/documents/{doc_id}/source")
async def kb_download_source(doc_id: str):
    try:
        path = await _run_sync(resolve_source_file_path, doc_id)
        if not path:
            raise _err("源文件不存在", 404)
        filename = os.path.basename(path)
        return FileResponse(
            path,
            media_type=_source_media_type(path),
            headers={
                "Content-Disposition": content_disposition_header("attachment", filename),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.get("/kb/documents/{doc_id}/source/preview")
async def kb_preview_source(doc_id: str):
    try:
        path = await _run_sync(resolve_source_file_path, doc_id)
        if not path:
            raise _err("源文件不存在", 404)
        if not _can_inline_preview(path):
            raise _err("该文件类型不支持在线预览，请下载查看", 400)
        filename = os.path.basename(path)
        return FileResponse(
            path,
            media_type=_source_media_type(path),
            headers={
                "Content-Disposition": content_disposition_header("inline", filename),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.get("/kb/documents/{doc_id}/chunks")
async def kb_get_chunks(doc_id: str):
    try:
        data = await _run_sync(get_kb_service().get_document_chunks, doc_id)
        return _ok(data)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.put("/kb/chunks/{point_id}")
async def kb_update_chunk(point_id: str, body: KbUpdateChunkBody):
    if not body.text.strip():
        raise _err("text required")
    try:
        await _run_sync(get_kb_service().update_chunk, point_id, body.text.strip())
        return _ok({"ok": True})
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.delete("/kb/chunks/{point_id}")
async def kb_delete_chunk(point_id: str):
    try:
        await _run_sync(get_kb_service().delete_chunk, point_id)
        return _ok({"ok": True})
    except ValueError as exc:
        raise _err(str(exc), 404) from exc
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.delete("/kb/documents/{doc_id}")
async def kb_delete_document(doc_id: str):
    try:
        n = await _run_sync(get_kb_service().delete_document, doc_id)
        return _ok({"deleted_chunks": n})
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.post("/kb/clear")
async def kb_clear():
    try:
        n = await _run_sync(get_kb_service().clear_all)
        return _ok({"deleted_vectors": n})
    except Exception as exc:
        raise _err(str(exc), 500) from exc


# ---------- 分组 ----------
@router.get("/kb/groups")
async def kb_list_groups():
    store = get_mysql_store()
    if not store:
        cfg = load_mysql_config()
        raise _err(
            f"MySQL 未启用，请检查 {CONFIG_PATH} 中 database.enabled 或 database.url "
            f"(当前 enabled={cfg.enabled})",
            503,
        )
    try:
        data = await _run_sync(store.list_groups)
        return _ok(data)
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.post("/kb/groups")
async def kb_create_group(body: KbGroupCreateBody):
    store = get_mysql_store()
    if not store:
        raise _err("MySQL 未启用", 503)
    try:
        data = await _run_sync(store.create_group, body.name, body.description, body.sort_order)
        return _ok(data)
    except ValueError as exc:
        raise _err(str(exc), 400) from exc
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.put("/kb/groups/{group_id}")
async def kb_update_group(group_id: int, body: KbGroupUpdateBody):
    store = get_mysql_store()
    if not store:
        raise _err("MySQL 未启用", 503)
    try:
        data = await _run_sync(
            store.update_group, group_id, body.name, body.description, body.sort_order
        )
        return _ok(data)
    except ValueError as exc:
        raise _err(str(exc), 400) from exc
    except Exception as exc:
        raise _err(str(exc), 500) from exc


@router.delete("/kb/groups/{group_id}")
async def kb_delete_group(group_id: int):
    store = get_mysql_store()
    if not store:
        raise _err("MySQL 未启用", 503)
    try:
        await _run_sync(store.delete_group, group_id)
        return _ok({"ok": True})
    except ValueError as exc:
        raise _err(str(exc), 400) from exc
    except Exception as exc:
        raise _err(str(exc), 500) from exc
