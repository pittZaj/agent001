"""SQLite 通用 CRUD 后端（给 Web 用）。

安全约束：
- 表名/列名走 _safe_ident 白名单校验，防 SQL 注入
- WHERE 子句使用参数化查询
- audit_log 表禁止 UI 改写（由 Agent 写入）
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "ksipms_dev.db"

PROTECTED_TABLES = {"audit_log"}  # 只读，UI 不让改


def _safe_ident(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _conn(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def list_tables() -> list[str]:
    if not DB_PATH.exists():
        return []
    with _conn(readonly=True) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r[0] for r in rows if not r[0].startswith("sqlite_")]


def get_columns(table: str) -> list[dict[str, Any]]:
    table = _safe_ident(table)
    with _conn(readonly=True) as c:
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {"name": r[1], "type": r[2], "notnull": bool(r[3]),
         "default": r[4], "pk": bool(r[5])}
        for r in rows
    ]


def primary_key_columns(table: str) -> list[str]:
    return [c["name"] for c in get_columns(table) if c["pk"]]


def query(table: str, *, where: str = "", limit: int = 100,
          offset: int = 0) -> tuple[list[str], list[list[Any]], int]:
    """读表。where 是 LIKE 'col=val and col2 LIKE %x%' 这种**用户自由文本**。
    我们不直接拼进 SQL；只允许形如 `col=value` / `col LIKE value` 的简单子句，
    用 `and` 连接，并参数化。
    """
    table = _safe_ident(table)
    columns = [c["name"] for c in get_columns(table)]

    sql = f"SELECT {', '.join(columns)} FROM {table}"
    params: list[Any] = []
    if where.strip():
        clauses, p = _parse_where(where, columns)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
            params.extend(p)
    sql += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    count_sql = f"SELECT COUNT(*) FROM {table}"
    count_params: list[Any] = []
    if where.strip():
        clauses, p = _parse_where(where, columns)
        if clauses:
            count_sql += " WHERE " + " AND ".join(clauses)
            count_params.extend(p)

    with _conn(readonly=True) as c:
        rows = c.execute(sql, params).fetchall()
        total = c.execute(count_sql, count_params).fetchone()[0]
    data = [[r[col] for col in columns] for r in rows]
    return columns, data, int(total)


def _parse_where(text: str, columns: list[str]) -> tuple[list[str], list[Any]]:
    """把 `col=val and col2 like %x% and col3 in (a,b)` 解析为参数化 SQL。"""
    clauses: list[str] = []
    params: list[Any] = []
    parts = re.split(r"\s+and\s+", text.strip(), flags=re.IGNORECASE)
    col_set = set(columns)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(
            r"(\w+)\s*(=|!=|>=|<=|>|<|like|LIKE|in|IN)\s*(.+)",
            part,
        )
        if not m:
            raise ValueError(f"无法解析 where 子句: {part!r}")
        col, op, val = m.group(1), m.group(2).upper(), m.group(3).strip()
        if col not in col_set:
            raise ValueError(f"未知列: {col!r}")
        if op == "IN":
            mlist = re.fullmatch(r"\(\s*(.+?)\s*\)", val)
            if not mlist:
                raise ValueError(f"IN 后期望 (a,b,...): {val!r}")
            items = [x.strip().strip("'\"") for x in mlist.group(1).split(",") if x.strip()]
            placeholders = ",".join("?" * len(items))
            clauses.append(f"{col} IN ({placeholders})")
            params.extend(items)
        else:
            clauses.append(f"{col} {op} ?")
            params.append(val.strip("'\""))
    return clauses, params


def insert(table: str, row: dict[str, Any]) -> dict[str, Any]:
    if table in PROTECTED_TABLES:
        raise PermissionError(f"{table} 受保护，不可手动插入")
    table = _safe_ident(table)
    cols = [_safe_ident(k) for k in row.keys()]
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT INTO {table}({', '.join(cols)}) VALUES ({placeholders})"
    with _conn(readonly=False) as c:
        c.execute(sql, list(row.values()))
        c.commit()
    return {"ok": True, "inserted": row}


def update(table: str, *, key: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    if table in PROTECTED_TABLES:
        raise PermissionError(f"{table} 受保护，不可手动更新")
    table = _safe_ident(table)
    if not key:
        raise ValueError("update 必须提供主键 key")
    set_cols = [_safe_ident(k) for k in values.keys()]
    where_cols = [_safe_ident(k) for k in key.keys()]
    sql = (f"UPDATE {table} SET "
           + ", ".join(f"{c}=?" for c in set_cols)
           + " WHERE " + " AND ".join(f"{c}=?" for c in where_cols))
    with _conn(readonly=False) as c:
        cur = c.execute(sql, list(values.values()) + list(key.values()))
        c.commit()
        return {"ok": True, "rows_affected": cur.rowcount}


def delete(table: str, *, key: dict[str, Any]) -> dict[str, Any]:
    if table in PROTECTED_TABLES:
        raise PermissionError(f"{table} 受保护，不可手动删除")
    table = _safe_ident(table)
    if not key:
        raise ValueError("delete 必须提供主键 key")
    where_cols = [_safe_ident(k) for k in key.keys()]
    sql = f"DELETE FROM {table} WHERE " + " AND ".join(f"{c}=?" for c in where_cols)
    with _conn(readonly=False) as c:
        cur = c.execute(sql, list(key.values()))
        c.commit()
    return {"ok": True, "rows_affected": cur.rowcount}


def stats() -> list[tuple[str, int]]:
    out = []
    for t in list_tables():
        try:
            with _conn(readonly=True) as c:
                n = c.execute(f"SELECT COUNT(*) FROM {_safe_ident(t)}").fetchone()[0]
            out.append((t, int(n)))
        except sqlite3.Error as e:
            out.append((t, -1))
    return out


def reset_database() -> str:
    """重跑 seed.py 完全重建数据库（开发期兜底）。"""
    seed_py = PROJECT_ROOT / "data" / "seed.py"
    if not seed_py.exists():
        return f"❌ seed.py not found: {seed_py}"
    res = subprocess.run(
        [sys.executable, str(seed_py)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    out = (res.stdout or "") + (res.stderr or "")
    return out.strip() or ("✅ done" if res.returncode == 0 else "❌ failed")
