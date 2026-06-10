"""阶段 5：端到端 3 个 Demo（基于真实平台）"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_adapter.client import reset_mcp_client
from skills import get_skill_registry
from skills.init import register_local_skills
from skills.mcp_skills import register_mcp_skills
from skills.vlm_judge_subgraph import register_vlm_judge_skill
from skills.alarm_skills import register_alarm_skills
from mcp_adapter.client import get_mcp_client
from graph.graph import build_graph


async def setup_no_kb():
    """跳过 KB 初始化，避免 BGE-M3 GPU 段错误干扰主线 demo"""
    registry = get_skill_registry()
    register_local_skills(registry)
    mcp_client = await get_mcp_client()
    registry.set_mcp_client(mcp_client)
    await register_mcp_skills(registry, mcp_client)
    register_vlm_judge_skill(registry)
    register_alarm_skills(registry)
    return registry


def _summary(state, max_chars=600):
    fr = state.get("final_response") or ""
    plan = state.get("plan", []) or []
    print(f"  plan 步骤数: {len(plan)}")
    for i, s in enumerate(plan):
        print(f"    [{i}] {s['task']} -> {s.get('status')}")
    print(f"  final_response: {fr[:max_chars]}{'...' if len(fr) > max_chars else ''}")


async def main():
    registry = await setup_no_kb()
    graph = build_graph()

    # ============ Demo 1：统计 + 可视化 ============
    print("\n" + "=" * 60)
    print("Demo 1: 统计每种 AI 告警类型数量并画柱状图")
    print("=" * 60)
    out1 = graph.invoke({"user_message": "统计每种 AI 告警类型的数量并画柱状图"})
    _summary(out1)

    # 检查图表
    found_chart = False
    for r in out1.get("tool_results", []) or []:
        if r.get("tool") == "visualize_alarms" and r.get("success"):
            data = r.get("result", {})
            if "image_base64" in data and data["image_base64"].startswith("data:image"):
                found_chart = True
                break
    print(f"  ✅ 图表生成: {found_chart}")

    # ============ Demo 2：复判闭环 ============
    print("\n" + "=" * 60)
    print("Demo 2: 复判最新一条 AI 告警并根据结论回写状态")
    print("=" * 60)

    # 先取一条最新事件 UUID
    list_r = await registry.invoke("ai_event_list", {"pageno": 1, "pagesize": 1}, {})
    if not list_r.get("events"):
        print("  [SKIP] 平台无 AI 事件")
    else:
        uid = list_r["events"][0]["uuid"]
        prompt = f"复判 AI 告警 {uid}，并根据复判结论回写它的状态"
        print(f"  目标事件: {uid}")
        out2 = graph.invoke({"user_message": prompt})
        _summary(out2)

        # 检查 verdict 是否被解析并写回
        verdict = None
        write_ok = False
        for r in out2.get("tool_results", []) or []:
            if r.get("tool") == "vlm_judge_alarm" and r.get("success"):
                verdict = r.get("result", {}).get("verdict")
            if r.get("tool") == "update_alarm_status" and r.get("success"):
                write_ok = bool(r.get("result", {}).get("success"))
        print(f"  ✅ VLM verdict: {verdict}, 回写成功: {write_ok}")

    # ============ Demo 3：仅 MCP 字段处理 ============
    print("\n" + "=" * 60)
    print("Demo 3: 查询 AI 摄像机/设备列表")
    print("=" * 60)
    out3 = graph.invoke({"user_message": "查询 AI 摄像机/视频设备列表，前 5 个"})
    _summary(out3)

    await reset_mcp_client()
    print("\n" + "=" * 60)
    print("[DONE] 3 个 Demo 全部跑完")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
