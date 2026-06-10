"""阶段 4 验证：Planner 能识别 19 个真实工具并生成合法计划"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_adapter.client import reset_mcp_client
from skills.init import init_skill_registry
from graph.nodes import planner_node


CASES = [
    "查最近 5 条 AI 告警",
    "统计最近的告警按类型分布并画柱状图",
    "查询 AI 设备列表",
    "未戴安全帽违反哪些规章制度？",
]


async def main() -> int:
    await init_skill_registry()
    failed = 0
    for q in CASES:
        print(f"\n>>> 用户: {q}")
        out = planner_node({"user_message": q})
        plan = out.get("plan", [])
        if not plan:
            print("    [FAIL] plan 为空"); failed += 1; continue
        for i, step in enumerate(plan):
            print(f"    [{i}] {step['task']}  args={step.get('args')}")
        # 校验：每个 task 必须存在于 registry
        from skills import get_skill_registry
        reg = get_skill_registry()
        bad = [s for s in plan if not reg.get(s["task"])]
        if bad:
            print(f"    [FAIL] 不存在的工具: {[s['task'] for s in bad]}")
            failed += 1
        else:
            print("    [OK]")

    await reset_mcp_client()
    print(f"\n{'='*40}\n结果: {len(CASES)-failed}/{len(CASES)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
