"""KSIPMS MCP Server

暴露告警、人员、录像查询工具，提供配置化权限控制和隐私保护。

启动方式：
    # stdio 模式（LangGraph 集成）
    python -m mcp_servers.ksipms_server

    # HTTP 模式（调试）
    python -m mcp_servers.ksipms_server --http --port 3000
"""
import argparse
import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ===================== 配置加载 =====================
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["ksipms_server"]


CONFIG = load_config()


# ===================== 数据库访问 =====================
def _resolve_db_path(rel_path: str) -> Path:
    """解析相对于项目根的数据库路径"""
    p = Path(rel_path)
    if not p.is_absolute():
        # 相对于 agent/ 目录
        root = Path(__file__).resolve().parent.parent
        p = root / rel_path
    return p


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    """只读连接"""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_conn(db_path: Path) -> sqlite3.Connection:
    """读写连接（仅用于 audit_log）"""
    return sqlite3.connect(str(db_path))


def _audit(db_path: Path, *, tool_name: str, args: dict) -> None:
    """审计日志（best-effort）"""
    try:
        conn = _rw_conn(db_path)
        try:
            args_digest = hashlib.sha1(
                json.dumps(args, sort_keys=True).encode()
            ).hexdigest()[:8]
            conn.execute(
                "INSERT INTO audit_log(alarm_id,action,operator_id,payload,ts) VALUES (?,?,?,?,?)",
                (
                    None,
                    "mcp_tool_call",
                    "mcp_server",
                    json.dumps({"tool": tool_name, "args_digest": args_digest}, ensure_ascii=False),
                    int(time.time()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # best-effort


def _date_to_epoch_range(date_str: str) -> tuple[int, int]:
    """YYYY-MM-DD → (UTC 当日 00:00, 次日 00:00) epoch 秒"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(dt.timestamp())
    return start, start + 86400


# ===================== 工具实现 =====================
def query_alarms_impl(date: str | None = None,
                      alarm_type: str | None = None,
                      camera_id: str | None = None) -> dict:
    """查询告警记录"""
    db_path = _resolve_db_path(CONFIG["db_path"])

    if not db_path.exists():
        return {"total": 0, "by_type": [], "items": [], "error": f"db not found: {db_path}"}

    # 构造查询（仅暴露白名单字段）
    allowed_fields = CONFIG["table_fields"]["alarms"]
    sql = [f"SELECT {', '.join(allowed_fields)} FROM alarms WHERE 1=1"]
    params: list[Any] = []

    if date:
        try:
            t0, t1 = _date_to_epoch_range(date)
            sql.append("AND ts_event >= ? AND ts_event < ?")
            params.extend([t0, t1])
        except ValueError:
            return {"total": 0, "by_type": [], "items": [], "error": f"invalid date: {date}"}
    else:
        # 默认最近7天
        sql.append("AND ts_event >= ?")
        params.append(int(time.time()) - 7 * 86400)

    if alarm_type:
        sql.append("AND alarm_type = ?")
        params.append(alarm_type)

    if camera_id:
        sql.append("AND camera_id = ?")
        params.append(camera_id)

    sql.append("ORDER BY ts_event DESC")
    sql.append(f"LIMIT {CONFIG['tools']['query_alarms']['max_results']}")

    try:
        conn = _ro_conn(db_path)
        try:
            rows = conn.execute(" ".join(sql), params).fetchall()
            by_type: dict[str, int] = {}
            for r in rows:
                by_type[r["alarm_type"]] = by_type.get(r["alarm_type"], 0) + 1
            items = [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"total": 0, "by_type": [], "items": [], "error": f"sqlite error: {e}"}

    _audit(db_path, tool_name="query_alarms", args={"date": date, "alarm_type": alarm_type, "camera_id": camera_id})

    return {
        "total": sum(by_type.values()),
        "by_type": [{"alarm_type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        "items": items,
        "error": None,
    }


def query_person_impl(person_id: str) -> dict:
    """查询人员信息 + 最近7天告警次数"""
    db_path = _resolve_db_path(CONFIG["db_path"])

    if not db_path.exists():
        return {"person": None, "recent_alarms": 0, "error": f"db not found: {db_path}"}

    allowed_fields = CONFIG["table_fields"]["persons"]

    try:
        conn = _ro_conn(db_path)
        try:
            row = conn.execute(
                f"SELECT {', '.join(allowed_fields)} FROM persons WHERE person_id = ?",
                (person_id,),
            ).fetchone()

            if not row:
                return {"person": None, "recent_alarms": 0, "error": "person not found"}

            cnt = conn.execute(
                "SELECT COUNT(*) FROM alarms WHERE person_id = ? AND ts_event >= ?",
                (person_id, int(time.time()) - 7 * 86400),
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"person": None, "recent_alarms": 0, "error": f"sqlite error: {e}"}

    _audit(db_path, tool_name="query_person", args={"person_id": person_id})

    return {"person": dict(row), "recent_alarms": int(cnt), "error": None}


def query_video_impl(camera_id: str, start_time: int | str, end_time: int | str) -> dict:
    """查询录像片段"""
    db_path = _resolve_db_path(CONFIG["db_path"])

    if not db_path.exists():
        return {"clips": [], "error": f"db not found: {db_path}"}

    def _to_epoch(x):
        if isinstance(x, int):
            return x
        try:
            return int(x)
        except (TypeError, ValueError):
            try:
                return int(datetime.fromisoformat(str(x)).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                return None

    s, e = _to_epoch(start_time), _to_epoch(end_time)
    if s is None or e is None:
        return {"clips": [], "error": "invalid start/end time"}

    allowed_fields = CONFIG["table_fields"]["video_clips"]

    try:
        conn = _ro_conn(db_path)
        try:
            rows = conn.execute(
                f"SELECT {', '.join(allowed_fields)} FROM video_clips "
                f"WHERE camera_id = ? AND ts_start >= ? AND ts_end <= ? "
                f"ORDER BY ts_start DESC LIMIT 50",
                (camera_id, s, e),
            ).fetchall()
            clips = [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"clips": [], "error": f"sqlite error: {e}"}

    _audit(db_path, tool_name="query_video", args={"camera_id": camera_id, "start_time": str(start_time), "end_time": str(end_time)})

    return {"clips": clips, "error": None}


# ===================== MCP Server =====================
server = Server("ksipms")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用工具"""
    tools_config = CONFIG["tools"]
    return [
        Tool(
            name="query_alarms",
            description=tools_config["query_alarms"]["description"],
            inputSchema=tools_config["query_alarms"]["parameters"],
        ),
        Tool(
            name="query_person",
            description=tools_config["query_person"]["description"],
            inputSchema=tools_config["query_person"]["parameters"],
        ),
        Tool(
            name="query_video",
            description=tools_config["query_video"]["description"],
            inputSchema=tools_config["query_video"]["parameters"],
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """调用工具"""
    if name == "query_alarms":
        result = query_alarms_impl(**arguments)
    elif name == "query_person":
        result = query_person_impl(**arguments)
    elif name == "query_video":
        result = query_video_impl(**arguments)
    else:
        result = {"error": f"unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


# ===================== 启动 =====================
async def main():
    parser = argparse.ArgumentParser(description="KSIPMS MCP Server")
    parser.add_argument("--http", action="store_true", help="HTTP 模式（调试）")
    parser.add_argument("--port", type=int, default=3000, help="HTTP 端口")
    args = parser.parse_args()

    if args.http:
        # HTTP 模式（需要额外依赖）
        print(f"[KSIPMS MCP Server] HTTP mode not implemented yet. Use stdio mode.")
        return

    # stdio 模式（标准 MCP）
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
