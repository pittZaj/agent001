"""
简化的 MVP 测试脚本（节省 token）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meta_agent.prompt_generator import PromptGenerator
from meta_agent.code_generator import CodeGenerator
from meta_agent.executor import Executor
from meta_agent.evaluator import Evaluator


def test_simple():
    """简单测试：只运行一轮，验证流程"""
    print("=" * 60)
    print("🧪 简化测试（MVP 验证）")
    print("=" * 60)

    # 定义简单任务
    task = {
        "name": "alarm_query_agent",
        "description": "查询告警记录",
        "tools": [
            {
                "name": "query_alarms",
                "description": "查询告警记录",
                "parameters": {
                    "date": "日期 YYYY-MM-DD"
                }
            }
        ],
        "test_cases": [
            {
                "input": "今天发生了哪几种告警？",
                "expected_tool": "query_alarms"
            }
        ]
    }

    print(f"任务: {task['name']}")
    print(f"工具: {task['tools'][0]['name']}")
    print(f"测试用例: {len(task['test_cases'])} 个")
    print()

    # 1. 生成 Prompt
    print("1️⃣  生成 System Prompt...")
    prompt_gen = PromptGenerator()
    prompt = prompt_gen.generate(task)
    print(f"   Prompt 长度: {len(prompt)} 字符")

    # 2. 生成代码
    print("2️⃣  生成 Agent 代码...")
    code_gen = CodeGenerator()
    code = code_gen.generate(prompt, task["tools"], task["name"])
    print(f"   代码长度: {len(code)} 字符")

    # 保存生成的代码
    artifacts_dir = Path(__file__).parent / "artifacts" / "generated_agents"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    code_file = artifacts_dir / f"{task['name']}.py"
    code_file.write_text(code, encoding="utf-8")
    print(f"   代码已保存: {code_file}")

    # 3. 执行测试
    print("3️⃣  执行测试...")
    executor = Executor(timeout=30)
    test_result = executor.run_tests(code, task["test_cases"])

    print(f"   通过: {test_result['passed']}")
    print(f"   失败: {test_result['failed']}")

    # 4. 评估
    print("4️⃣  评估性能...")
    evaluator = Evaluator()
    metrics = evaluator.evaluate(test_result)

    print(f"   工具准确率: {metrics['tool_accuracy']:.1%}")
    print(f"   执行成功率: {metrics['execution_success']:.1%}")
    print(f"   综合得分: {metrics['overall_score']:.1%}")

    # 5. 显示详细结果
    if test_result.get("details"):
        print("\n📋 测试详情:")
        for detail in test_result["details"]:
            status = "✅" if detail["passed"] else "❌"
            print(f"   {status} 测试 {detail['test_id']}: {detail['input']}")
            if detail.get("error"):
                print(f"      错误: {detail['error'][:200]}")
            if detail.get("reasons"):
                for reason in detail["reasons"]:
                    print(f"      原因: {reason}")

    print("\n" + "=" * 60)
    if metrics["overall_score"] >= 0.5:
        print("✅ 基础流程验证成功！")
    else:
        print("⚠️  需要进一步调试")
    print("=" * 60)

    return {
        "success": metrics["overall_score"] >= 0.5,
        "metrics": metrics,
        "code_file": str(code_file)
    }


if __name__ == "__main__":
    result = test_simple()

    if result["success"]:
        print(f"\n🎉 MVP 验证通过！")
        print(f"生成的 Agent 代码: {result['code_file']}")
        print("\n下一步:")
        print("1. 查看生成的代码")
        print("2. 手动运行生成的 Agent 测试")
        print("3. 根据实际效果调整 Prompt 模板")
    else:
        print(f"\n⚠️  MVP 需要调试")
        print("查看详细日志排查问题")
