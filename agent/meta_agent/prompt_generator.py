"""
元提示词生成器

根据任务描述和工具定义，生成 LangGraph Agent 的 System Prompt
"""
from typing import Dict, List, Any


class PromptGenerator:
    """元提示词生成器"""

    def __init__(self, prompt_library_path: str = None):
        """初始化

        Args:
            prompt_library_path: prompt 模板库路径（可选）
        """
        self.prompt_library_path = prompt_library_path

    def generate(self, task: Dict[str, Any]) -> str:
        """生成 system prompt

        Args:
            task: 任务定义，包含：
                - name: Agent 名称
                - description: 任务描述
                - tools: 可用工具列表
                - test_cases: 测试用例（可选）

        Returns:
            生成的 system prompt
        """
        name = task.get("name", "agent")
        description = task.get("description", "")
        tools = task.get("tools", [])

        # 构建工具列表描述
        tools_desc = self._format_tools(tools)

        # 生成 prompt
        prompt = f"""你是一个专业的任务执行智能体：{name}

# 任务职责
{description}

# 可用工具
{tools_desc}

# 工作流程
1. 仔细分析用户的请求，理解其意图
2. 将复杂任务拆解为可执行的步骤
3. 按顺序调用合适的工具完成每个步骤
4. 汇总结果，生成结构化的回复

# 规则
- 只使用上述列出的工具，不要臆造不存在的工具
- 工具参数必须从用户请求中提取或合理推断
- 如果信息不足，主动询问用户
- 返回的结果要清晰、准确、有条理

# 输出格式
始终以 JSON 格式返回执行计划：
{{
  "plan": [
    {{"task": "tool_name", "args": {{"param": "value"}}, "reason": "为什么需要这一步"}},
    ...
  ]
}}
"""
        return prompt

    def _format_tools(self, tools: List[Any]) -> str:
        """格式化工具列表

        Args:
            tools: 工具定义列表

        Returns:
            格式化的工具描述
        """
        if not tools:
            return "（无可用工具）"

        lines = []
        for i, tool in enumerate(tools, 1):
            if isinstance(tool, str):
                # 简单字符串形式
                lines.append(f"{i}. {tool}")
            elif isinstance(tool, dict):
                # 字典形式，包含详细定义
                tool_name = tool.get("name", "unknown")
                tool_desc = tool.get("description", "")
                tool_params = tool.get("parameters", {})

                lines.append(f"{i}. **{tool_name}**: {tool_desc}")

                # 添加参数说明
                if tool_params:
                    lines.append("   参数：")
                    for param_name, param_info in tool_params.items():
                        param_desc = param_info if isinstance(param_info, str) else param_info.get("description", "")
                        lines.append(f"   - {param_name}: {param_desc}")

        return "\n".join(lines)

    def optimize(self, prompt: str, feedback: str) -> str:
        """根据反馈优化 prompt

        Args:
            prompt: 原始 prompt
            feedback: 失败原因或改进建议

        Returns:
            优化后的 prompt
        """
        # MVP 版本：简单追加反馈到规则部分
        optimization = f"\n\n# 注意事项（根据测试反馈优化）\n{feedback}\n"
        return prompt + optimization


if __name__ == "__main__":
    # 测试代码
    generator = PromptGenerator()

    task = {
        "name": "alarm_query_agent",
        "description": "查询安全生产平台的告警记录",
        "tools": [
            {
                "name": "query_alarms",
                "description": "查询告警记录，支持按日期、类型、摄像机筛选",
                "parameters": {
                    "date": "日期 YYYY-MM-DD",
                    "alarm_type": "告警类型（可选）：smoking/no_helmet/phone/no_mask",
                    "camera_id": "摄像机 ID（可选）"
                }
            }
        ]
    }

    prompt = generator.generate(task)
    print(prompt)
    print("\n" + "="*60 + "\n")

    # 测试优化
    feedback = "- 工具调用时，date 参数格式错误，应为 YYYY-MM-DD\n- 返回结果应包含告警数量统计"
    optimized = generator.optimize(prompt, feedback)
    print(optimized)
