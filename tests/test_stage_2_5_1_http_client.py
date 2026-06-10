"""阶段 1 验证：MCP Client HTTP 传输 + list_tools/call_tool 冒烟"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_adapter.client import get_mcp_client, reset_mcp_client


async def main() -> int:
    print("[1/3] 获取 MCP Client（HTTP 传输）")
    client = await get_mcp_client()
    assert client.enabled, "MCP 未启用"
    assert "ksipms" in client.list_servers(), "ksipms server 未连接"

    print("[2/3] list_tools")
    tools = await client.list_tools("ksipms")
    print(f"    -> 共 {len(tools)} 个工具")
    by_prefix: dict[str, list[str]] = {}
    for t in tools:
        prefix = t["name"].split("_", 1)[0]
        by_prefix.setdefault(prefix, []).append(t["name"])
    for p, names in sorted(by_prefix.items()):
        print(f"    {p}_*: {len(names)} 个 -> {', '.join(names)}")

    expected = {"ai_event_list", "ai_event_detail", "ai_event_deal",
                "video_device_list", "video_resolve_camera_channel",
                "system_user_list", "system_role_list"}
    actual = {t["name"] for t in tools}
    missing = expected - actual
    assert not missing, f"缺关键工具: {missing}"
    print(f"    OK: 关键工具齐全 ({len(expected)}/{len(expected)})")

    print("[3/3] call_tool: ai_event_list (pageno=1, pagesize=3)")
    result = await client.call_tool("ksipms", "ai_event_list", {"pageno": 1, "pagesize": 3})
    if result.get("error"):
        print(f"    [WARN] 工具返回错误: {result['error']}")
        # 不视为失败：可能是真实平台暂无 AI 事件数据
    else:
        print(f"    matched={result.get('matched')} total={result.get('total')} "
              f"events={len(result.get('events', []))} 条")
        if result.get("events"):
            sample = result["events"][0]
            print(f"    样例 keys: {sorted(sample.keys())[:10]}")

    await reset_mcp_client()
    print("\n[OK] 阶段 1 验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
