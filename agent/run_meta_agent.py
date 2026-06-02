"""
元智能体主运行脚本

整合 prompt 生成、代码生成、执行、评估、反馈分析的完整流程
"""
import json
import sys
from pathlib import Path
from typing import Dict, Any

# 添加 meta_agent 到路径
sys.path.insert(0, str(Path(__file__).parent))

from meta_agent.prompt_generator import PromptGenerator
from meta_agent.code_generator import CodeGenerator
from meta_agent.executor import Executor
from meta_agent.evaluator import Evaluator
from meta_agent.feedback_analyzer import FeedbackAnalyzer


def run_meta_agent(
    task: Dict[str, Any],
    max_iterations: int = 5,
    target_score: float = 0.8,
    save_artifacts: bool = True
) -> Dict[str, Any]:
    """运行元智能体，自动生成并优化子 Agent

    Args:
        task: 任务定义
        max_iterations: 最大迭代次数
        target_score: 目标得分
        save_artifacts: 是否保存中间产物

    Returns:
        生成结果
    """
    print("=" * 60)
    print("🤖 元智能体启动")
    print("=" * 60)
    print(f"任务: {task.get('name')}")
    print(f"描述: {task.get('description')}")
    print(f"工具: {', '.join([t if isinstance(t, str) else t.get('name', '') for t in task.get('tools', [])])}")
    print(f"测试用例数: {len(task.get('test_cases', []))}")
    print(f"目标得分: {target_score}")
    print("=" * 60)
    print()

    # 初始化组件
    prompt_gen = PromptGenerator()
    code_gen = CodeGenerator()
    executor = Executor(timeout=60)
    evaluator = Evaluator()
    analyzer = FeedbackAnalyzer()

    # 初始生成
    prompt = prompt_gen.generate(task)
    best_prompt = prompt
    best_code = None
    best_score = 0.0
    best_metrics = {}

    for iteration in range(1, max_iterations + 1):
        print(f"\n[第 {iteration} 轮迭代]")
        print("-" * 60)

        # 1. 生成代码
        print("1️⃣  生成代码...")
        code = code_gen.generate(prompt, task.get("tools", []), task.get("name", "agent"))

        # 2. 执行测试
        print("2️⃣  执行测试...")
        test_result = executor.run_tests(code, task.get("test_cases", []))

        # 3. 评估
        print("3️⃣  评估性能...")
        metrics = evaluator.evaluate(test_result)

        print(f"   工具准确率: {metrics['tool_accuracy']:.1%}")
        print(f"   执行成功率: {metrics['execution_success']:.1%}")
        print(f"   综合得分: {metrics['overall_score']:.1%}")

        # 4. 更新最佳结果
        if metrics['overall_score'] > best_score:
            best_score = metrics['overall_score']
            best_prompt = prompt
            best_code = code
            best_metrics = metrics
            print(f"   ✅ 新最佳得分: {best_score:.1%}")

        # 5. 判断是否达标
        if evaluator.meets_threshold(metrics, target_score):
            print(f"\n🎉 达到目标得分 {target_score:.1%}，迭代结束！")
            break

        # 6. 分析反馈
        print("4️⃣  分析失败原因...")
        feedback = analyzer.analyze(test_result)
        print(f"   改进建议:\n{feedback}")

        # 7. 优化 prompt
        if iteration < max_iterations:
            print("5️⃣  优化 Prompt...")
            prompt = prompt_gen.optimize(prompt, feedback)

    # 保存最佳版本
    if save_artifacts and best_code:
        artifacts_dir = Path(__file__).parent / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        agent_name = task.get("name", "agent")

        # 保存代码
        code_file = artifacts_dir / "generated_agents" / f"{agent_name}.py"
        code_file.parent.mkdir(exist_ok=True)
        code_file.write_text(best_code, encoding="utf-8")

        # 保存 prompt
        prompt_file = artifacts_dir / "best_prompts" / f"{agent_name}_prompt.txt"
        prompt_file.parent.mkdir(exist_ok=True)
        prompt_file.write_text(best_prompt, encoding="utf-8")

        # 保存指标
        metrics_file = artifacts_dir / "test_results" / f"{agent_name}_metrics.json"
        metrics_file.parent.mkdir(exist_ok=True)
        metrics_file.write_text(json.dumps({
            "task": task,
            "metrics": best_metrics,
            "score": best_score
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n💾 最佳版本已保存:")
        print(f"   代码: {code_file}")
        print(f"   Prompt: {prompt_file}")
        print(f"   指标: {metrics_file}")

    print("\n" + "=" * 60)
    print(f"🏁 元智能体执行完成")
    print(f"   最佳得分: {best_score:.1%}")
    print(f"   迭代次数: {iteration}")
    print("=" * 60)

    return {
        "success": best_score >= target_score,
        "score": best_score,
        "metrics": best_metrics,
        "iterations": iteration,
        "code": best_code,
        "prompt": best_prompt
    }


if __name__ == "__main__":
    # 示例任务：告警查询 Agent
    task = {
        "name": "alarm_query_agent",
        "description": "查询安全生产平台的告警记录，支持按日期、类型筛选",
        "tools": [
            {
                "name": "query_alarms",
                "description": "查询告警记录",
                "parameters": {
                    "date": "日期 YYYY-MM-DD",
                    "alarm_type": "告警类型（可选）：smoking/no_helmet/phone/no_mask"
                }
            }
        ],
        "test_cases": [
            {
                "input": "今天发生了哪几种告警？",
                "expected_tool": "query_alarms"
            },
            {
                "input": "查询2026-06-01的抽烟告警",
                "expected_tool": "query_alarms"
            },
            {
                "input": "最近有多少次未戴安全帽的告警？",
                "expected_tool": "query_alarms"
            }
        ]
    }

    result = run_meta_agent(task, max_iterations=5, target_score=0.8)

    if result["success"]:
        print("\n✅ 任务成功完成！")
    else:
        print(f"\n⚠️  未达到目标得分，当前最佳: {result['score']:.1%}")
