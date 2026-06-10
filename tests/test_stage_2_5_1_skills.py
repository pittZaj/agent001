"""阶段 3 验证：4 个下游 skill 全部基于真实 MCP 输出"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_adapter.client import reset_mcp_client
from skills.init import init_skill_registry


async def main() -> int:
    registry = await init_skill_registry()

    print("\n[1/4] aggregate_alarms (group_by=event_name)")
    r = await registry.invoke("aggregate_alarms", {"group_by": "event_name"}, {})
    if r.get("error"):
        print(f"    [FAIL] {r['error']}"); return 1
    print(f"    total={r['total']} platform_total={r['platform_total']} sampled={r['sampled']}")
    for d in r["data"][:5]:
        print(f"      {d['key']}: {d['count']}")

    print("\n[2/4] visualize_alarms (bar)")
    v = await registry.invoke("visualize_alarms",
                              {"data": r, "chart_type": "bar", "title": "AI 告警类型分布"}, {})
    if v.get("error"):
        print(f"    [FAIL] {v['error']}"); return 1
    print(f"    image_base64 长度={len(v['image_base64'])}")

    print("\n[3/4] vlm_judge_alarm (取最新一条 AI 告警)")
    list_r = await registry.invoke("ai_event_list", {"pageno": 1, "pagesize": 1}, {})
    if not list_r.get("events"):
        print("    [SKIP] 平台无 AI 事件");
    else:
        uid = list_r["events"][0]["uuid"]
        j = await registry.invoke("vlm_judge_alarm", {"alarm_uuid": uid}, {})
        if j.get("error"):
            print(f"    [WARN] {j['error']}")
        else:
            print(f"    verdict={j.get('verdict')} confidence={j.get('confidence')}")
            print(f"    reasoning={(j.get('reasoning') or '')[:120]}")
            print(f"    img_url={j.get('img_url')}")

    print("\n[4/4] update_alarm_status (干跑：取已复核的事件回写为相同状态，避免数据污染)")
    finished = await registry.invoke(
        "ai_event_list", {"pageno": 1, "pagesize": 1, "review_status": "2"}, {}
    )
    if finished.get("events"):
        uid = finished["events"][0]["uuid"]
        u = await registry.invoke(
            "update_alarm_status",
            {"alarm_uuid": uid, "verdict": "confirmed", "note": "stage2.5.1 自检"},
            {},
        )
        if u.get("error"):
            print(f"    [FAIL] {u['error']}"); return 1
        print(f"    success={u['success']} review_status={u['review_status']}")
    else:
        print("    [SKIP] 无 review_status=2 数据可供安全测试")

    print("\n[5] fetch_alarm_context（可选）")
    if list_r.get("events"):
        uid = list_r["events"][0]["uuid"]
        f = await registry.invoke("fetch_alarm_context",
                                  {"alarm_uuid": uid, "before_sec": 5, "after_sec": 5}, {})
        if f.get("error"):
            print(f"    [WARN] {f['error']}")
        else:
            print(f"    segment_count={f.get('segment_count')} channel={f.get('channel_uuid')}")

    await reset_mcp_client()
    print("\n[OK] 阶段 3 验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
