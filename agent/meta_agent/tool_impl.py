"""生成的 Agent 共用的工具实现：真连 SQLite (read-only)。

每个工具都符合 RULES §2 契约：返回 dict，永不 raise。
audit_log 写入是 best-effort（写失败不影响主流程）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _resolve_db_path(data_source: str) -> Path:
    """`sqlite:data/ksipms_dev.db.alarms` → Path。"""
    if data_source.startswith("sqlite:"):
        rel = data_source[len("sqlite:"):].split(".")[0] + ".db"
    else:
        rel = data_source
    p = Path(rel)
    if not p.is_absolute():
        # 相对项目根（agent/agent/）
        p = Path(__file__).resolve().parent.parent / p
    return p


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def _audit(db_path: Path, *, alarm_id: str | None, action: str,
           operator_id: str, payload: dict) -> None:
    try:
        conn = _rw_conn(db_path)
        try:
            conn.execute(
                "INSERT INTO audit_log(alarm_id,action,operator_id,payload,ts) VALUES (?,?,?,?,?)",
                (alarm_id, action, operator_id,
                 json.dumps(payload, ensure_ascii=False), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # best-effort, RULES §4


def _args_digest(args: dict) -> str:
    s = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def _date_to_epoch_range(date_str: str) -> tuple[int, int]:
    """`YYYY-MM-DD` → (UTC 当日 00:00, 次日 00:00) 的 epoch 秒。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(dt.timestamp())
    return start, start + 86400


def query_alarms(date: str | None = None,
                 alarm_type: str | None = None,
                 camera_id: str | None = None,
                 *, _ctx: dict | None = None,
                 _data_source: str = "sqlite:data/ksipms_dev.db") -> dict:
    """查询告警记录。

    Args:
        date: YYYY-MM-DD（UTC）；None 则不限定日期，默认返回最近 7 天
        alarm_type: 类型 type_code (smoking/no_helmet/...)；None 则不限
        camera_id: 摄像头 ID；None 则不限

    Returns:
        {"total": int, "by_type": [{"alarm_type":..,"count":..}],
         "items": [...up to 20...], "error": None}
    """
    ctx = _ctx or {}
    trace_id = ctx.get("trace_id", "anonymous")
    db_path = _resolve_db_path(_data_source)

    if not db_path.exists():
        return {"total": 0, "by_type": [], "items": [],
                "error": f"db not found: {db_path}"}

    sql = ["SELECT alarm_uuid, alarm_type, camera_id, area_id, severity, status,",
           "       ts_event, alarm_desc, model_conf",
           "FROM alarms WHERE 1=1"]
    params: list[Any] = []

    if date:
        try:
            t0, t1 = _date_to_epoch_range(date)
            sql.append("AND ts_event >= ? AND ts_event < ?")
            params.extend([t0, t1])
        except ValueError:
            return {"total": 0, "by_type": [], "items": [],
                    "error": f"invalid date format (need YYYY-MM-DD): {date!r}"}
    else:
        # 默认窗口：最近 7 天
        sql.append("AND ts_event >= ?")
        params.append(int(time.time()) - 7 * 86400)

    if alarm_type:
        sql.append("AND alarm_type = ?")
        params.append(alarm_type)

    if camera_id:
        sql.append("AND camera_id = ?")
        params.append(camera_id)

    sql.append("ORDER BY ts_event DESC")

    try:
        conn = _ro_conn(db_path)
        try:
            rows = conn.execute(" ".join(sql), params).fetchall()
            by_type: dict[str, int] = {}
            for r in rows:
                by_type[r["alarm_type"]] = by_type.get(r["alarm_type"], 0) + 1
            items = [dict(r) for r in rows[:20]]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"total": 0, "by_type": [], "items": [], "error": f"sqlite error: {e}"}

    _audit(
        db_path,
        alarm_id=None,
        action="tool_call",
        operator_id=f"agent:{ctx.get('agent_name','unknown')}",
        payload={
            "trace_id": trace_id,
            "tool": "query_alarms",
            "args_digest": _args_digest({"date": date, "alarm_type": alarm_type, "camera_id": camera_id}),
        },
    )

    return {
        "total": sum(by_type.values()),
        "by_type": [{"alarm_type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        "items": items,
        "error": None,
    }


def query_video(camera_id: str, start_time: int | str, end_time: int | str,
                *, _ctx: dict | None = None,
                _data_source: str = "sqlite:data/ksipms_dev.db") -> dict:
    """查录像片段（占位实现）。"""
    db_path = _resolve_db_path(_data_source)
    if not db_path.exists():
        return {"clips": [], "error": f"db not found: {db_path}"}

    def _to_epoch(x):
        if isinstance(x, int):
            return x
        try:
            return int(x)
        except (TypeError, ValueError):
            try:
                return int(datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                return None

    s, e = _to_epoch(start_time), _to_epoch(end_time)
    if s is None or e is None:
        return {"clips": [], "error": "invalid start/end time"}

    try:
        conn = _ro_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT clip_id, alarm_id, camera_id, ts_start, ts_end, file_path "
                "FROM video_clips WHERE camera_id=? AND ts_start>=? AND ts_end<=? "
                "ORDER BY ts_start DESC LIMIT 50",
                (camera_id, s, e),
            ).fetchall()
            clips = [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as ex:
        return {"clips": [], "error": f"sqlite error: {ex}"}

    return {"clips": clips, "error": None}


def query_person(person_id: str,
                 *, _ctx: dict | None = None,
                 _data_source: str = "sqlite:data/ksipms_dev.db") -> dict:
    """查人员信息 + 最近 7 天告警次数。"""
    db_path = _resolve_db_path(_data_source)
    if not db_path.exists():
        return {"person": None, "recent_alarms": 0, "error": f"db not found: {db_path}"}

    try:
        conn = _ro_conn(db_path)
        try:
            row = conn.execute(
                "SELECT person_id, name, department, role FROM persons WHERE person_id=?",
                (person_id,),
            ).fetchone()
            if not row:
                return {"person": None, "recent_alarms": 0, "error": "person not found"}
            cnt = conn.execute(
                "SELECT COUNT(*) FROM alarms WHERE person_id=? AND ts_event>=?",
                (person_id, int(time.time()) - 7 * 86400),
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"person": None, "recent_alarms": 0, "error": f"sqlite error: {e}"}

    return {"person": dict(row), "recent_alarms": int(cnt), "error": None}


# 工具注册表（生成的 Agent 通过 name 反查实现）
TOOL_REGISTRY = {
    "query_alarms": query_alarms,
    "query_video": query_video,
    "query_person": query_person,
}


def call_tool(name: str, args: dict, *, ctx: dict | None = None,
              data_source: str = "sqlite:data/ksipms_dev.db") -> dict:
    """统一工具调用入口；未知工具返回 error 而非 raise。"""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(**args, _ctx=ctx, _data_source=data_source)
    except TypeError as e:
        return {"error": f"bad args for {name}: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # 烟测
    print("today:", query_alarms(date=datetime.utcnow().strftime("%Y-%m-%d")))
    print("smoking:", query_alarms(alarm_type="smoking"))
