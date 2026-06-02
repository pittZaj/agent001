"""
评估器

计算 Agent 性能指标
"""
from typing import Dict, Any, List


class Evaluator:
    """Agent 性能评估器"""

    def __init__(self):
        pass

    def evaluate(self, test_result: Dict[str, Any]) -> Dict[str, float]:
        """评估测试结果

        Args:
            test_result: 执行器返回的测试结果

        Returns:
            评估指标：
                - tool_accuracy: 工具调用准确率 (0-1)
                - execution_success: 执行成功率 (0-1)
                - overall_score: 综合得分 (0-1)
        """
        if test_result.get("error"):
            # 执行失败
            return {
                "tool_accuracy": 0.0,
                "execution_success": 0.0,
                "overall_score": 0.0
            }

        success_rate = test_result.get("success_rate", 0.0)
        passed = test_result.get("passed", 0)
        failed = test_result.get("failed", 0)
        total = passed + failed

        if total == 0:
            return {
                "tool_accuracy": 0.0,
                "execution_success": 0.0,
                "overall_score": 0.0
            }

        # 工具调用准确率 = 成功率
        tool_accuracy = success_rate

        # 执行成功率（未抛出异常）
        execution_success = self._calculate_execution_success(test_result)

        # 综合得分（加权平均）
        overall_score = (
            tool_accuracy * 0.6 +       # 工具调用占 60%
            execution_success * 0.4      # 执行成功占 40%
        )

        return {
            "tool_accuracy": round(tool_accuracy, 3),
            "execution_success": round(execution_success, 3),
            "overall_score": round(overall_score, 3)
        }

    def _calculate_execution_success(self, test_result: Dict[str, Any]) -> float:
        """计算执行成功率

        Args:
            test_result: 测试结果

        Returns:
            执行成功率（未抛出异常的测试占比）
        """
        details = test_result.get("details", [])
        if not details:
            return 0.0

        success_count = sum(1 for d in details if not d.get("error"))
        return success_count / len(details)

    def meets_threshold(self, metrics: Dict[str, float], threshold: float = 0.8) -> bool:
        """判断是否达到目标阈值

        Args:
            metrics: 评估指标
            threshold: 阈值（默认 0.8）

        Returns:
            是否达标
        """
        overall_score = metrics.get("overall_score", 0.0)
        return overall_score >= threshold


if __name__ == "__main__":
    # 测试评估器
    evaluator = Evaluator()

    # 模拟测试结果
    test_result = {
        "success_rate": 0.8,
        "passed": 4,
        "failed": 1,
        "details": [
            {"test_id": 1, "passed": True, "error": None},
            {"test_id": 2, "passed": True, "error": None},
            {"test_id": 3, "passed": True, "error": None},
            {"test_id": 4, "passed": True, "error": None},
            {"test_id": 5, "passed": False, "error": None}
        ],
        "error": None
    }

    metrics = evaluator.evaluate(test_result)
    print(f"评估指标: {metrics}")
    print(f"是否达标: {evaluator.meets_threshold(metrics)}")
