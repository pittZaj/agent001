"""
执行器

运行生成的 Agent 代码，捕获输出和错误
"""
import sys
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Dict, Any, List


class Executor:
    """Agent 代码执行器"""

    def __init__(self, timeout: int = 60):
        """初始化

        Args:
            timeout: 执行超时时间（秒）
        """
        self.timeout = timeout

    def run_tests(self, code: str, test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        """运行测试用例

        Args:
            code: Agent 代码
            test_cases: 测试用例列表，每个包含：
                - input: 用户输入
                - expected_tool: 期望调用的工具（可选）
                - expected_output: 期望输出片段（可选）

        Returns:
            测试结果：
                - success_rate: 成功率
                - passed: 通过的测试数
                - failed: 失败的测试数
                - details: 每个测试的详细结果
                - error: 执行错误（如果有）
        """
        if not test_cases:
            return {
                "success_rate": 0.0,
                "passed": 0,
                "failed": 0,
                "details": [],
                "error": "没有测试用例"
            }

        # 将代码写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            temp_file = f.name
            f.write(code)

        try:
            results = []
            passed = 0
            failed = 0

            for i, test_case in enumerate(test_cases):
                user_input = test_case.get("input", "")
                expected_tool = test_case.get("expected_tool")
                expected_output = test_case.get("expected_output")

                # 构造测试脚本
                test_script = f"""
import sys
sys.path.insert(0, '{Path(temp_file).parent}')

from {Path(temp_file).stem} import run

result = run("{user_input}")
print(result)
"""

                # 执行测试
                test_result = self._run_script(test_script)

                # 评估结果
                test_passed = True
                reasons = []

                if test_result.get("error"):
                    test_passed = False
                    reasons.append(f"执行错误: {test_result['error']}")
                else:
                    output = test_result.get("output", "")

                    # 检查期望工具
                    if expected_tool:
                        if expected_tool not in output:
                            test_passed = False
                            reasons.append(f"未调用期望工具: {expected_tool}")

                    # 检查期望输出
                    if expected_output:
                        if expected_output not in output:
                            test_passed = False
                            reasons.append(f"输出不包含期望内容: {expected_output}")

                if test_passed:
                    passed += 1
                else:
                    failed += 1

                results.append({
                    "test_id": i + 1,
                    "input": user_input,
                    "passed": test_passed,
                    "output": test_result.get("output", ""),
                    "error": test_result.get("error"),
                    "reasons": reasons
                })

            success_rate = passed / len(test_cases) if test_cases else 0.0

            return {
                "success_rate": success_rate,
                "passed": passed,
                "failed": failed,
                "details": results,
                "error": None
            }

        except Exception as e:
            return {
                "success_rate": 0.0,
                "passed": 0,
                "failed": len(test_cases),
                "details": [],
                "error": str(e)
            }
        finally:
            # 清理临时文件
            Path(temp_file).unlink(missing_ok=True)

    def _run_script(self, script: str) -> Dict[str, Any]:
        """运行 Python 脚本

        Args:
            script: Python 脚本内容

        Returns:
            执行结果：output（stdout）、error（stderr）
        """
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            return {
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }

        except subprocess.TimeoutExpired:
            return {
                "output": "",
                "error": f"执行超时（{self.timeout}秒）"
            }
        except Exception as e:
            return {
                "output": "",
                "error": str(e)
            }


if __name__ == "__main__":
    # 测试执行器
    executor = Executor(timeout=30)

    # 简单的测试代码
    test_code = '''
def run(user_message: str):
    return {
        "response": f"处理: {user_message}",
        "plan": [{"task": "query_alarms", "args": {"date": "2026-06-02"}}],
        "tool_results": [],
        "error": None
    }
'''

    test_cases = [
        {
            "input": "今天的告警",
            "expected_tool": "query_alarms"
        }
    ]

    result = executor.run_tests(test_code, test_cases)
    print(json.dumps(result, ensure_ascii=False, indent=2))
