"""
反馈分析器

分析测试失败原因，生成改进建议
"""
from typing import Dict, Any, List


class FeedbackAnalyzer:
    """测试反馈分析器"""

    def __init__(self):
        pass

    def analyze(self, test_result: Dict[str, Any]) -> str:
        """分析测试结果，生成改进建议

        Args:
            test_result: 测试结果

        Returns:
            改进建议文本
        """
        if not test_result.get("details"):
            return "无测试详情，无法分析"

        suggestions = []

        # 分析每个失败的测试
        for detail in test_result["details"]:
            if not detail.get("passed"):
                reasons = detail.get("reasons", [])
                test_input = detail.get("input", "")

                for reason in reasons:
                    suggestion = self._generate_suggestion(reason, test_input, detail)
                    if suggestion:
                        suggestions.append(suggestion)

        if not suggestions:
            return "所有测试通过，无需改进"

        # 去重
        unique_suggestions = list(set(suggestions))

        return "\n".join([f"- {s}" for s in unique_suggestions])

    def _generate_suggestion(self, reason: str, test_input: str, detail: Dict[str, Any]) -> str:
        """根据失败原因生成具体建议

        Args:
            reason: 失败原因
            test_input: 测试输入
            detail: 测试详情

        Returns:
            改进建议
        """
        # 模式匹配生成建议
        if "未调用期望工具" in reason:
            tool_name = reason.split(":")[-1].strip()
            return f"Prompt 中需要明确说明何时调用 {tool_name} 工具，并提供调用示例"

        if "输出不包含期望内容" in reason:
            expected = reason.split(":")[-1].strip()
            return f"Prompt 中需要强调输出格式，确保包含 {expected}"

        if "执行错误" in reason:
            error = detail.get("error", "")
            if "Timeout" in error:
                return "执行超时，考虑优化任务拆解，减少冗余步骤"
            elif "KeyError" in error or "AttributeError" in error:
                return "工具参数错误，Prompt 中需要添加参数格式示例和类型说明"
            elif "JSONDecodeError" in error:
                return "LLM 输出格式不符合 JSON，需要在 Prompt 中强调严格遵守 JSON Schema"
            else:
                return f"执行异常: {error[:100]}，需要排查代码逻辑"

        return "需要进一步分析失败原因"


if __name__ == "__main__":
    # 测试反馈分析器
    analyzer = FeedbackAnalyzer()

    test_result = {
        "success_rate": 0.6,
        "passed": 3,
        "failed": 2,
        "details": [
            {
                "test_id": 1,
                "input": "今天的告警",
                "passed": False,
                "reasons": ["未调用期望工具: query_alarms"]
            },
            {
                "test_id": 2,
                "input": "查询录像",
                "passed": False,
                "reasons": ["执行错误: KeyError: 'camera_id'"]
            }
        ]
    }

    feedback = analyzer.analyze(test_result)
    print("改进建议：")
    print(feedback)
