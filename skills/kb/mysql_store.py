"""知识库元数据 MySQL 存储（分组、文档索引）。

连接信息从 config.yaml 的 database 段读取；向量数据仍在 Qdrant。
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

import pymysql
from loguru import logger
from pymysql.cursors import DictCursor

from utils import CONFIG, CONFIG_PATH

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "kb_mysql.sql"
_lock = threading.Lock()
_store: "KbMySQLStore | None" = None


@dataclass
class MySQLConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "ksagent"
    charset: str = "utf8mb4"


def _parse_database_url(url: str) -> MySQLConfig:
    """解析 mysql+aiomysql://user:pass@host:port/db"""
    raw = (url or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    parsed = urlparse(f"mysql://{raw}")
    db = (parsed.path or "/ksagent").lstrip("/") or "ksagent"
    return MySQLConfig(
        enabled=True,
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=unquote(parsed.username or "root"),
        password=unquote(parsed.password or ""),
        database=db,
    )


def _coerce_bool(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if value is False or value == 0 or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value)


def _mysql_enabled(db: dict[str, Any]) -> bool:
    """enabled 显式为 false 时关闭；否则有 url 或 host 即视为启用。"""
    if "enabled" in db and not _coerce_bool(db.get("enabled")):
        return False
    if str(db.get("url") or "").strip():
        return True
    return bool(db.get("host"))


def load_mysql_config() -> MySQLConfig:
    db = CONFIG.get("database") or {}
    if not _mysql_enabled(db):
        return MySQLConfig(enabled=False)
    if db.get("url"):
        cfg = _parse_database_url(str(db["url"]))
        cfg.enabled = True
        return cfg
    return MySQLConfig(
        enabled=True,
        host=str(db.get("host", "127.0.0.1")),
        port=int(db.get("port", 3306)),
        user=str(db.get("user", "root")),
        password=str(db.get("password", "")),
        database=str(db.get("name") or db.get("database") or "ksagent"),
        charset=str(db.get("charset", "utf8mb4")),
    )


class KbMySQLStore:
    def __init__(self, cfg: MySQLConfig):
        self.cfg = cfg
        self._schema_ready = False

    def _connect(self):
        return pymysql.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.user,
            password=self.cfg.password,
            database=self.cfg.database,
            charset=self.cfg.charset,
            cursorclass=DictCursor,
            autocommit=True,
        )

    @contextmanager
    def _cursor(self) -> Iterator[DictCursor]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with _lock:
            if self._schema_ready:
                return
            if not _SCHEMA_PATH.is_file():
                raise FileNotFoundError(f"KB schema 文件不存在: {_SCHEMA_PATH}")
            sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            statements = [s.strip() for s in re.split(r";\s*\n", sql) if s.strip()]
            statements = [
                s
                for s in statements
                if not re.match(r"^\s*CREATE\s+DATABASE\b", s, re.I)
                and not re.match(r"^\s*USE\s+", s, re.I)
            ]
            with self._cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                self._migrate_document_columns(cur)
            self._schema_ready = True
            logger.info(f"KB MySQL schema 已就绪: {self.cfg.database}")

    def _migrate_document_columns(self, cur) -> None:
        """兼容已有库：补列 source_file_path 等。"""
        alters = [
            "ALTER TABLE kb_document ADD COLUMN source_file_path VARCHAR(512) NOT NULL DEFAULT ''",
            "ALTER TABLE kb_document ADD COLUMN file_size BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE kb_document ADD COLUMN chunk_strategy VARCHAR(32) NOT NULL DEFAULT 'fixed_size'",
            "ALTER TABLE kb_document ADD COLUMN chunk_size INT NOT NULL DEFAULT 300",
            "ALTER TABLE kb_document ADD COLUMN chunk_overlap INT NOT NULL DEFAULT 50",
        ]
        for sql in alters:
            try:
                cur.execute(sql)
            except pymysql.err.OperationalError as exc:
                if exc.args[0] != 1060:
                    raise

    # ---------- 分组 ----------
    def list_groups(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at,
                       COUNT(d.doc_id) AS doc_count
                FROM kb_group g
                LEFT JOIN kb_document d ON d.group_id = g.id
                GROUP BY g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at
                ORDER BY g.sort_order ASC, g.id ASC
                """
            )
            rows = cur.fetchall() or []
        return [self._row_group(r) for r in rows]

    def create_group(self, name: str, description: str = "", sort_order: int = 0) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("分组名称不能为空")
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO kb_group (name, description, sort_order) VALUES (%s, %s, %s)",
                (name, description.strip(), sort_order),
            )
            gid = cur.lastrowid
            cur.execute("SELECT * FROM kb_group WHERE id=%s", (gid,))
            row = cur.fetchone()
        return self._row_group({**row, "doc_count": 0})

    def update_group(self, group_id: int, name: str | None = None, description: str | None = None, sort_order: int | None = None) -> dict[str, Any]:
        self.ensure_schema()
        fields: list[str] = []
        params: list[Any] = []
        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("分组名称不能为空")
            fields.append("name=%s")
            params.append(n)
        if description is not None:
            fields.append("description=%s")
            params.append(description.strip())
        if sort_order is not None:
            fields.append("sort_order=%s")
            params.append(sort_order)
        if not fields:
            raise ValueError("无更新字段")
        params.append(group_id)
        with self._cursor() as cur:
            cur.execute(f"UPDATE kb_group SET {', '.join(fields)} WHERE id=%s", params)
            if cur.rowcount == 0:
                raise ValueError(f"分组不存在: {group_id}")
            cur.execute(
                """
                SELECT g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at,
                       COUNT(d.doc_id) AS doc_count
                FROM kb_group g
                LEFT JOIN kb_document d ON d.group_id = g.id
                WHERE g.id=%s
                GROUP BY g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at
                """,
                (group_id,),
            )
            row = cur.fetchone()
        return self._row_group(row)

    def delete_group(self, group_id: int) -> None:
        if group_id == 1:
            raise ValueError("默认分组不可删除")
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute("UPDATE kb_document SET group_id=NULL WHERE group_id=%s", (group_id,))
            cur.execute("DELETE FROM kb_group WHERE id=%s", (group_id,))
            if cur.rowcount == 0:
                raise ValueError(f"分组不存在: {group_id}")

    def get_group(self, group_id: int) -> dict[str, Any] | None:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at,
                       COUNT(d.doc_id) AS doc_count
                FROM kb_group g
                LEFT JOIN kb_document d ON d.group_id = g.id
                WHERE g.id=%s
                GROUP BY g.id, g.name, g.description, g.sort_order, g.created_at, g.updated_at
                """,
                (group_id,),
            )
            row = cur.fetchone()
        return self._row_group(row) if row else None

    # ---------- 文档元数据 ----------
    def insert_document(
        self,
        doc_id: str,
        title: str,
        category: str,
        filename: str,
        chunks_count: int,
        group_id: int | None = None,
        source_file_path: str = "",
        file_size: int = 0,
        chunk_strategy: str = "fixed_size",
        chunk_size: int = 300,
        chunk_overlap: int = 50,
    ) -> None:
        self.ensure_schema()
        if group_id is not None and self.get_group(group_id) is None:
            raise ValueError(f"分组不存在: {group_id}")
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_document
                (doc_id, group_id, title, category, filename, source_file_path, file_size,
                 chunk_strategy, chunk_size, chunk_overlap, chunks_count, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'indexed')
                """,
                (
                    doc_id, group_id, title, category, filename, source_file_path, file_size,
                    chunk_strategy, chunk_size, chunk_overlap, chunks_count,
                ),
            )

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT d.doc_id, d.group_id, g.name AS group_name, d.title, d.category,
                       d.filename, d.source_file_path, d.file_size, d.chunk_strategy,
                       d.chunk_size, d.chunk_overlap, d.chunks_count, d.status,
                       d.created_at, d.updated_at
                FROM kb_document d
                LEFT JOIN kb_group g ON g.id = d.group_id
                WHERE d.doc_id=%s
                """,
                (doc_id,),
            )
            row = cur.fetchone()
        return self._row_document(row) if row else None

    def update_chunks_count(self, doc_id: str, count: int) -> None:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE kb_document SET chunks_count=%s WHERE doc_id=%s",
                (count, doc_id),
            )

    def update_document_group(self, doc_id: str, group_id: int | None) -> dict[str, Any]:
        """更新文档所属分组。"""
        self.ensure_schema()
        if group_id is not None and self.get_group(group_id) is None:
            raise ValueError(f"分组不存在: {group_id}")
        with self._cursor() as cur:
            cur.execute(
                "UPDATE kb_document SET group_id=%s WHERE doc_id=%s",
                (group_id, doc_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"文档不存在: {doc_id}")
        doc = self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        return doc

    def delete_document(self, doc_id: str) -> str | None:
        """删除文档元数据，返回源文件路径供清理。"""
        self.ensure_schema()
        source_path = None
        with self._cursor() as cur:
            cur.execute(
                "SELECT source_file_path FROM kb_document WHERE doc_id=%s",
                (doc_id,),
            )
            row = cur.fetchone()
            if row:
                source_path = row.get("source_file_path") or None
            cur.execute("DELETE FROM kb_document WHERE doc_id=%s", (doc_id,))
        return source_path or None

    def list_source_paths(self) -> list[str]:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute(
                "SELECT source_file_path FROM kb_document WHERE source_file_path<>''"
            )
            rows = cur.fetchall() or []
        return [str(r["source_file_path"]) for r in rows if r.get("source_file_path")]

    def list_documents(self, group_id: int | None = None) -> list[dict[str, Any]]:
        self.ensure_schema()
        sql = """
            SELECT d.doc_id, d.group_id, g.name AS group_name, d.title, d.category,
                   d.filename, d.source_file_path, d.file_size, d.chunk_strategy,
                   d.chunk_size, d.chunk_overlap, d.chunks_count, d.status,
                   d.created_at, d.updated_at
            FROM kb_document d
            LEFT JOIN kb_group g ON g.id = d.group_id
        """
        params: tuple[Any, ...] = ()
        if group_id is not None:
            sql += " WHERE d.group_id=%s"
            params = (group_id,)
        sql += " ORDER BY d.updated_at DESC, d.created_at DESC"
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
        return [self._row_document(r) for r in rows]

    def clear_documents(self) -> int:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM kb_document")
            n = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute("DELETE FROM kb_document")
        return n

    def count_documents(self) -> int:
        self.ensure_schema()
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM kb_document")
            return int((cur.fetchone() or {}).get("c") or 0)

    @staticmethod
    def _row_group(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description") or "",
            "sort_order": row.get("sort_order") or 0,
            "doc_count": int(row.get("doc_count") or 0),
            "created_at": _fmt_dt(row.get("created_at")),
            "updated_at": _fmt_dt(row.get("updated_at")),
        }

    @staticmethod
    def _row_document(row: dict[str, Any]) -> dict[str, Any]:
        source_path = row.get("source_file_path") or ""
        file_size = int(row.get("file_size") or 0)
        return {
            "doc_id": row["doc_id"],
            "group_id": row.get("group_id"),
            "group_name": row.get("group_name") or "",
            "title": row.get("title") or "",
            "category": row.get("category") or "",
            "filename": row.get("filename") or "",
            "source_file_path": source_path,
            "has_source": bool(source_path),
            "file_size": file_size,
            "chunk_strategy": row.get("chunk_strategy") or "fixed_size",
            "chunk_size": int(row.get("chunk_size") or 300),
            "chunk_overlap": int(row.get("chunk_overlap") or 50),
            "chunks_count": int(row.get("chunks_count") or 0),
            "status": row.get("status") or "indexed",
            "created_at": _fmt_dt(row.get("created_at")),
            "updated_at": _fmt_dt(row.get("updated_at")),
        }


def _fmt_dt(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def get_mysql_store() -> KbMySQLStore | None:
    global _store
    cfg = load_mysql_config()
    if not cfg.enabled:
        return None
    if _store is None:
        _store = KbMySQLStore(cfg)
    return _store
