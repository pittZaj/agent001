"""KSIPMS 只写 MCP Server（受控写操作）。

仅暴露 update_alarm_status 工具，用于大模型复判后回写告警处理结论。
严格权限控制：只能 UPDATE alarms 表的指定字段，status 只能为 closed/false_alarm，
每次写操作强制记录 audit_log。

启动方式：
    python -m mcp_servers.ksipms_write_server
"""
import asyncio
import json
import sqlite3
import time
from pathlib import Path

import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


def load_config() -> dict:
    config_path = Path(__file__).parent / "config_write.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["ksipms_write_server"]


CONFIG = load_config()
OPERATOR_ID = "agent:vlm_judge"  # 智能体写操作统一标识


def _resolve_db_path(rel_path: str) -> Path:
    p = Path(rel_path)
    if not p.is_absolute():
        root = Path(__file__).resolve().parent.parent
        p = root / rel_path
    return p


def update_alarm_status_impl(alarm_uuid: str, status: str, note: str = "") -> dict:
    """更新告警状态（受控写）"""
    # 1. 校验 status 白名单
    allowed_status = CONFIG["allowed_status_values"]
    if status not in allowed_status:
        return {"success": False, "error": f"status 必须是 {allowed_status} 之一，收到: {status}"}

    db_path = _resolve_db_path(CONFIG["db_path"])
    if not db_path.exists():
        return {"success": False, "error": f"db not found: {db_path}"}

    now = int(time.time())
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # 2. 校验告警存在
            cur = conn.execute("SELECT alarm_uuid, status FROM alarms WHERE alarm_uuid=?", (alarm_uuid,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"告警不存在: {alarm_uuid}"}
            old_status = row[1]

            # 3. 强制审计（写操作前）
            conn.execute(
                "INSERT INTO audit_log(alarm_id, action, operator_id, payload, ts) VALUES (?,?,?,?,?)",
                (
                    alarm_uuid, "agent_update_status", OPERATOR_ID,
                    json.dumps({"old_status": old_status, "new_status": status, "note": note}, ensure_ascii=False),
                    now,
                ),
            )

            # 4. 受控更新（只动白名单字段）
            conn.execute(
                "UPDATE alarms SET status=?, processed_note=?, processed_at=?, processed_by=? WHERE alarm_uuid=?",
                (status, note, now, OPERATOR_ID, alarm_uuid),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"success": False, "error": f"sqlite error: {e}"}

    return {
        "success": True, "error": None,
        "alarm_uuid": alarm_uuid,
        "old_status": old_status, "new_status": status,
        "processed_by": OPERATOR_ID, "processed_at": now,
    }


# ===================== MCP Server =====================
server = Server("ksipms_write")


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools_config = CONFIG["tools"]
    return [
        Tool(
            name="update_alarm_status",
            description=tools_config["update_alarm_status"]["description"],
            inputSchema=tools_config["update_alarm_status"]["parameters"],
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "update_alarm_status":
        result = update_alarm_status_impl(**arguments)
    else:
        result = {"success": False, "error": f"unknown tool: {name}"}
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
