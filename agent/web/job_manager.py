"""任务状态持久化（SQLite）。

web 提交的任务、autoctl 启动的进程信息、最终产出指标都存到独立小库
`web/jobs.db`，与业务库 `data/ksipms_dev.db` 完全分开。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "jobs.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    agent_name    TEXT NOT NULL,
    spec_path     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'submitted',
    pid           INTEGER,
    log_path      TEXT,
    artifacts_dir TEXT,
    created_at    INTEGER NOT NULL,
    finished_at   INTEGER,
    score         REAL,
    metrics_json  TEXT,
    register_json TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_name   ON jobs(agent_name);
"""


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def insert_job(*, job_id: str, agent_name: str, spec_path: str,
               pid: int | None, log_path: str, artifacts_dir: str,
               status: str = "running", notes: str = "") -> None:
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO jobs(
                job_id, agent_name, spec_path, status, pid, log_path,
                artifacts_dir, created_at, notes)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (job_id, agent_name, spec_path, status, pid, log_path,
              artifacts_dir, int(time.time()), notes))


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def update_status(job_id: str, status: str, *,
                  finished_at: int | None = None,
                  score: float | None = None,
                  metrics_json: str | None = None,
                  register_json: str | None = None,
                  notes: str | None = None) -> None:
    fields = ["status = ?"]
    params: list[Any] = [status]
    if finished_at is not None:
        fields.append("finished_at = ?"); params.append(finished_at)
    if score is not None:
        fields.append("score = ?"); params.append(score)
    if metrics_json is not None:
        fields.append("metrics_json = ?"); params.append(metrics_json)
    if register_json is not None:
        fields.append("register_json = ?"); params.append(register_json)
    if notes is not None:
        fields.append("notes = ?"); params.append(notes)
    params.append(job_id)
    with conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", params)


def refresh_from_artifacts(job_id: str, artifacts_root: Path) -> dict[str, Any] | None:
    """如果 artifacts/<job_id>/REGISTER.json 已存在，把指标同步到 jobs 表。"""
    register = artifacts_root / job_id / "REGISTER.json"
    if not register.exists():
        # 检查 PID 是否还活
        job = get_job(job_id)
        if job and job["pid"]:
            try:
                __import__("os").kill(job["pid"], 0)
                return job  # 还在跑
            except ProcessLookupError:
                update_status(job_id, "failed", finished_at=int(time.time()),
                              notes="进程已退出但无 REGISTER.json")
        return get_job(job_id)
    try:
        info = json.loads(register.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return get_job(job_id)
    status = "success" if info.get("passed_acceptance") else "failed"
    update_status(
        job_id, status,
        finished_at=int(time.time()),
        score=info.get("score"),
        metrics_json=json.dumps(info.get("metrics", {}), ensure_ascii=False),
        register_json=json.dumps(info, ensure_ascii=False),
    )
    return get_job(job_id)
