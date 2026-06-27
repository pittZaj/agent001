"""planner_prompt 模块单元测试

验证提示词分层后的完整性：
1. 关键约束字符串存在（防幻觉规则、event_type 编码、时间规则）
2. 稳定前缀与动态后缀正确拼接
3. 时间计算准确性
"""
import pytest
from datetime import datetime
from graph.planner_prompt import (
    build_stable_prefix,
    build_dynamic_suffix,
    build_planner_system_prompt
)


class TestPlannerPrompt:
    """planner_prompt 模块测试"""

    def test_stable_prefix_contains_key_constraints(self):
        """验证稳定前缀包含所有关键约束"""
        tools_text = "- ai_event_list: 查询 AI 告警\n- video_device_list: 查询设备"
        catalog_text = "- `ET03002` = 违规抽烟\n- `ET03007` = 未戴安全帽"
        catalog_names = "违规抽烟、未戴安全帽"

        stable = build_stable_prefix(tools_text, catalog_text, catalog_names)

        # 防幻觉规则编号
        assert "1. 先把用户说的告警名**语义匹配**到上表" in stable
        assert "2. **能匹配到上表**" in stable
        assert "3. **无法匹配到上表**" in stable
        assert "4. 区分两种" in stable

        # 关键约束
        assert "绝对不要猜一个编码" in stable
        assert "必须真的去查" in stable
        assert "平台不支持识别" in stable

        # event_type 示例编码
        assert "ET03002" in stable  # 违规抽烟
        assert "ET03007" in stable  # 未戴安全帽

        # 字段说明
        assert "event_type" in stable
        assert "time_start" in stable
        assert "level" in stable
        assert "level=red" in stable

        # 输出格式
        assert "仅返回 JSON 数组" in stable
        assert "step_0" in stable  # 步骤间传参示例

        # 常见任务模板
        assert "aggregate_alarms" in stable
        assert "visualize_alarms" in stable
        assert "vlm_judge_alarm" in stable

    def test_dynamic_suffix_contains_time_rules(self):
        """验证动态后缀包含时间规则"""
        now = datetime(2026, 6, 27, 14, 30, 0)
        dynamic = build_dynamic_suffix(now)

        # 时间值
        assert "2026-06-27" in dynamic  # 今天
        assert "2026-06-26" in dynamic  # 昨天
        assert "2026-06-20" in dynamic  # 7天前
        assert "2026" in dynamic  # 年份

        # 时间规则
        assert "当前真实时间" in dynamic
        assert "时间筛选规则" in dynamic
        assert "查最近N条" in dynamic
        assert "绝对不要自己加 time_start/time_end" in dynamic
        assert "年份必须是 2026 年" in dynamic

    def test_complete_prompt_structure(self):
        """验证完整提示词结构正确"""
        tools_text = "test_tools"
        catalog_text = "test_catalog"
        catalog_names = "test_names"
        now = datetime(2026, 6, 27, 14, 30, 0)

        prompt = build_planner_system_prompt(
            tools_text, catalog_text, catalog_names, now
        )

        # 动态段在前（关键：利于 prefix cache）
        assert prompt.index("⏰ 当前真实时间") < prompt.index("平台支持的 AI 告警类型")

        # 包含所有关键段落
        assert "当前真实时间" in prompt
        assert "平台支持的 AI 告警类型" in prompt
        assert "可用工具" in prompt
        assert "真实平台关键字段" in prompt
        assert "常见任务模板" in prompt
        assert "约束" in prompt

    def test_time_calculation_accuracy(self):
        """验证时间计算准确性"""
        now = datetime(2026, 6, 27, 14, 30, 0)
        dynamic = build_dynamic_suffix(now)

        # 今天
        assert "**今天**：2026-06-27" in dynamic

        # 昨天
        assert "**昨天**：2026-06-26" in dynamic

        # 最近7天（2026-06-20 14:30:00）
        assert "最近 7 天/一周起点：2026-06-20 14:30:00" in dynamic

        # 最近30天（2026-05-28 14:30:00）
        assert "最近 30 天/一个月起点：2026-05-28 14:30:00" in dynamic

        # 最近2小时（2026-06-27 12:30:00）
        assert "最近 2 小时起点：2026-06-27 12:30:00" in dynamic

    def test_防幻觉规则3_direct_response(self):
        """验证防幻觉规则3的 direct_response 模板存在"""
        tools_text = ""
        catalog_text = ""
        catalog_names = "test"

        stable = build_stable_prefix(tools_text, catalog_text, catalog_names)

        # 规则3的完整模板
        assert 'task":"direct_response' in stable
        assert '平台不支持识别' in stable


if __name__ == "__main__":
    # 运行所有测试
    pytest.main([__file__, "-v"])
