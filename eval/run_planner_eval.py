"""轻量回归评测 - planner 层主入口

执行所有用例，对 planner_node 产出的 plan 做断言，生成对比报告。
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.cases import get_all_cases, EvalCase
from graph.state import AgentState
from graph.nodes import planner_node


# ========================= 断言逻辑 =========================

def check_tools_match(plan: list[dict], expect_tools: list[str]) -> tuple[bool, str]:
    """检查 plan 的 task 序列是否包含期望的工具序列（子集匹配）"""
    actual_tools = [step.get("task") for step in plan]
    # 子集匹配：expect_tools 按序出现在 actual_tools 中即可
    try:
        idx = 0
        for expected in expect_tools:
            while idx < len(actual_tools) and actual_tools[idx] != expected:
                idx += 1
            if idx >= len(actual_tools):
                return False, f"期望 {expect_tools} 在 {actual_tools} 中按序出现"
            idx += 1
        return True, ""
    except Exception as e:
        return False, f"工具序列匹配异常: {e}"


def check_args_contains(plan: list[dict], expect: dict) -> tuple[bool, str]:
    """检查指定步骤的 args 是否包含期望的键值"""
    for step_idx, expected_args in expect.items():
        if step_idx >= len(plan):
            return False, f"步骤 {step_idx} 不存在（plan 共 {len(plan)} 步）"
        actual_args = plan[step_idx].get("args", {})
        for key, expected_val in expected_args.items():
            if key not in actual_args:
                return False, f"步骤 {step_idx} 缺少 args.{key}"
            if actual_args[key] != expected_val:
                return False, f"步骤 {step_idx} args.{key}={actual_args[key]}，期望 {expected_val}"
    return True, ""


def check_forbid_keys(plan: list[dict], forbid: dict) -> tuple[bool, str]:
    """检查指定步骤的 args 是否出现禁止的键（防幻觉）"""
    for step_idx, forbid_keys in forbid.items():
        if step_idx >= len(plan):
            continue  # 步骤不存在则无需检查
        actual_args = plan[step_idx].get("args", {})
        for key in forbid_keys:
            if key in actual_args:
                return False, f"步骤 {step_idx} 不应有 args.{key}（防幻觉违规）"
    return True, ""


def check_time_args_year(plan: list[dict], case: EvalCase) -> tuple[bool, str]:
    """检查时间参数中的年份是否为当前年（防用2024/2025猜测）"""
    if "时间解析" not in case.tag:
        return True, ""  # 仅对时间解析类用例检查
    current_year = datetime.now().year
    for step_idx, step in enumerate(plan):
        args = step.get("args", {})
        for key in ["time_start", "time_end"]:
            if key in args:
                val = args[key]
                if isinstance(val, str) and len(val) >= 4:
                    year_str = val[:4]
                    try:
                        year = int(year_str)
                        if year != current_year:
                            return False, f"步骤 {step_idx} {key}={val} 年份非当前年 {current_year}"
                    except ValueError:
                        pass
    return True, ""


def check_direct_response(plan: list[dict]) -> bool:
    """检查是否走 direct_response 分支"""
    return (len(plan) == 1
            and plan[0].get("task") == "direct_response")


def evaluate_case(case: EvalCase) -> dict[str, Any]:
    """评测单条用例，返回结果字典"""
    logger.info(f"[{case.id}] 开始评测: {case.query}")

    # 构造 AgentState（仅填 user_message，空 messages）
    state = AgentState(
        user_message=case.query,
        messages=[],
        plan=[],
        current_task_idx=0,
        tool_results=[],
        step_outputs={},
        final_response="",
        session_id="eval",
        trace_id=f"eval-{case.id}",
    )

    # 调用 planner_node（会触发真实 LLM）
    try:
        result = planner_node(state)
        plan = result.get("plan", [])
    except Exception as e:
        logger.exception(f"[{case.id}] planner_node 调用异常")
        return {
            "case_id": case.id,
            "query": case.query,
            "tag": case.tag,
            "pass": False,
            "reason": f"planner_node 异常: {type(e).__name__}: {e}",
            "plan": None,
        }

    # 执行断言
    failures = []

    # 1. 工具序列匹配
    ok, msg = check_tools_match(plan, case.expect_tools)
    if not ok:
        failures.append(f"工具序列不符: {msg}")

    # 2. args 包含期望键值
    if case.expect_args_contains:
        ok, msg = check_args_contains(plan, case.expect_args_contains)
        if not ok:
            failures.append(f"args 键值不符: {msg}")

    # 3. 禁止键检查
    if case.forbid_args_keys:
        ok, msg = check_forbid_keys(plan, case.forbid_args_keys)
        if not ok:
            failures.append(f"禁止键出现: {msg}")

    # 4. direct_response 检查
    if case.must_be_direct_response:
        if not check_direct_response(plan):
            failures.append("期望 direct_response 但未命中")

    # 5. 时间参数年份检查
    ok, msg = check_time_args_year(plan, case)
    if not ok:
        failures.append(f"时间年份错误: {msg}")

    passed = len(failures) == 0
    logger.info(f"[{case.id}] {'✅ PASS' if passed else '❌ FAIL'}")
    if not passed:
        logger.warning(f"[{case.id}] 失败原因: {'; '.join(failures)}")

    return {
        "case_id": case.id,
        "query": case.query,
        "tag": case.tag,
        "pass": passed,
        "reason": "; ".join(failures) if failures else "",
        "plan": [{"task": s.get("task"), "args": s.get("args")} for s in plan],
    }


# ========================= Baseline Diff =========================

def load_baseline(baseline_path: Path) -> dict:
    """加载基线文件"""
    if not baseline_path.exists():
        return {}
    with baseline_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_baseline(baseline_path: Path, results: list[dict]):
    """保存基线（仅保存 case_id + pass 状态）"""
    baseline = {r["case_id"]: r["pass"] for r in results}
    with baseline_path.open("w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)


def diff_with_baseline(results: list[dict], baseline: dict) -> dict:
    """对比当前结果与基线，返回回退/改进/保持统计"""
    regressed = []  # 回退：之前 pass 现在 fail
    improved = []   # 改进：之前 fail 现在 pass
    stable = []     # 保持：pass→pass 或 fail→fail
    new_cases = []  # 新用例

    for r in results:
        cid = r["case_id"]
        current_pass = r["pass"]
        if cid not in baseline:
            new_cases.append(cid)
        else:
            baseline_pass = baseline[cid]
            if baseline_pass and not current_pass:
                regressed.append(cid)
            elif not baseline_pass and current_pass:
                improved.append(cid)
            else:
                stable.append(cid)

    return {
        "regressed": regressed,
        "improved": improved,
        "stable": stable,
        "new_cases": new_cases,
    }


# ========================= 报告生成 =========================

def generate_report(results: list[dict], diff_info: dict, baseline_existed: bool) -> str:
    """生成 Markdown 格式报告"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    failed = total - passed

    lines = [
        "# Planner 层回归评测报告",
        "",
        f"**生成时间**: {timestamp}",
        f"**用例总数**: {total}",
        f"**通过**: {passed} / {total} ({passed/total*100:.1f}%)",
        f"**失败**: {failed}",
        "",
    ]

    # Baseline 对比
    if baseline_existed:
        lines.extend([
            "## 📊 与 Baseline 对比",
            "",
            f"- 🔴 **回退** (pass→fail): {len(diff_info['regressed'])} 条",
            f"- 🟢 **改进** (fail→pass): {len(diff_info['improved'])} 条",
            f"- ⚪ **保持**: {len(diff_info['stable'])} 条",
            f"- 🆕 **新增用例**: {len(diff_info['new_cases'])} 条",
            "",
        ])
        if diff_info["regressed"]:
            lines.append("### ⚠️ 回退用例（需关注！）")
            lines.append("")
            for cid in diff_info["regressed"]:
                r = next(x for x in results if x["case_id"] == cid)
                lines.append(f"- `{cid}`: {r['query']}")
                lines.append(f"  - 失败原因: {r['reason']}")
            lines.append("")
    else:
        lines.extend([
            "## 🆕 首次运行（baseline 已生成）",
            "",
            "本次为首次运行，已将结果保存为 baseline.json，后续运行将与之对比。",
            "",
        ])

    # 详细结果
    lines.extend([
        "## 📝 详细结果",
        "",
        "| ID | 查询 | 标签 | 状态 | 失败原因 |",
        "|----|----|------|------|---------|",
    ])
    for r in results:
        status = "✅ PASS" if r["pass"] else "❌ FAIL"
        reason = r["reason"] if not r["pass"] else "-"
        lines.append(f"| {r['case_id']} | {r['query'][:30]}... | {r['tag']} | {status} | {reason[:50]}... |")

    lines.append("")
    lines.append("---")
    lines.append("**说明**: 本报告仅测 planner 规划结构，不测真实平台数据正确性。")

    return "\n".join(lines)


# ========================= 主入口 =========================

def main():
    """主入口"""
    logger.info("=" * 60)
    logger.info("轻量回归评测 - Planner 层")
    logger.info("=" * 60)

    # 检查 LLM 可达性
    try:
        from utils import CONFIG
        llm_url = CONFIG["llm"]["base_url"]
        logger.info(f"LLM 服务: {llm_url}")
        # 简单心跳（实际 planner_node 会真实调用）
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        sys.exit(1)

    # 加载用例
    cases = get_all_cases()
    logger.info(f"加载 {len(cases)} 条用例")

    # 逐条评测
    results = []
    for case in cases:
        result = evaluate_case(case)
        results.append(result)

    # Baseline 对比
    eval_dir = Path(__file__).parent
    baseline_path = eval_dir / "baseline.json"
    baseline_existed = baseline_path.exists()
    baseline = load_baseline(baseline_path) if baseline_existed else {}
    diff_info = diff_with_baseline(results, baseline)

    # 保存当前结果为新 baseline
    save_baseline(baseline_path, results)

    # 生成报告
    report_md = generate_report(results, diff_info, baseline_existed)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = eval_dir / f"report_{timestamp_str}.md"
    report_path.write_text(report_md, encoding="utf-8")
    logger.info(f"报告已生成: {report_path}")

    # 控制台输出摘要
    print("\n" + "=" * 60)
    print(report_md.split("## 📝 详细结果")[0])  # 仅输出摘要部分
    print("=" * 60)
    print(f"完整报告: {report_path}")

    # 退出码：有回退项时非 0
    if diff_info["regressed"]:
        logger.error(f"⚠️ 检测到 {len(diff_info['regressed'])} 条回退用例，退出码 1")
        sys.exit(1)
    else:
        logger.info("✅ 无回退用例，退出码 0")
        sys.exit(0)


if __name__ == "__main__":
    main()
