"""评测用例集 - planner 层回归

从 COMPREHENSIVE_TEST_CASES.md 提炼，聚焦 planner 规划结构与防幻觉约束。
每条用例断言：工具序列、参数键值、禁止键、direct_response 分支。
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EvalCase:
    """单条评测用例"""
    id: str                      # 用例编号（如 "T01"）
    query: str                   # 用户问句
    expect_tools: list[str]      # 期望 plan 里出现的 task 序列（按序，子集匹配即可）
    expect_args_contains: dict = field(default_factory=dict)
    # 期望某步 args 包含的键值，如 {0: {"event_type": "ET03007"}}
    forbid_args_keys: dict = field(default_factory=dict)
    # 禁止某步出现的键（防幻觉），如 {0: ["time_start", "time_end"]}
    must_be_direct_response: bool = False
    # 期望走 direct_response 分支（如平台不支持类型）
    tag: str = ""                # 维度标签（用于分类统计）


# ========================= 用例集定义 =========================
# 从现有 COMPREHENSIVE_TEST_CASES.md 提炼 + 补充发散场景
# 覆盖维度：简单查询 / 类型匹配 / 防幻觉 / 最近N条vs统计 / 时间解析 /
#          统计画图 / 复判回写 / 知识库 / 闲聊 / 设备查询 / 录像回溯

EVAL_CASES = [
    # ===== 1. 简单查询（无时间不可自加） =====
    EvalCase(
        id="T01",
        query="查询未戴安全帽的告警",
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"event_type": "ET03007"}},  # 未戴安全帽 ET03007
        forbid_args_keys={0: ["time_start", "time_end"]},  # 用户无时间→不可自加
        tag="简单查询-无时间",
    ),
    EvalCase(
        id="T02",
        query="查询吸烟告警",
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"event_type": "ET03002"}},  # 违规抽烟 ET03002
        forbid_args_keys={0: ["time_start", "time_end"]},
        tag="简单查询-类型匹配",
    ),

    # ===== 2. 平台不支持类型（防幻觉规则3） =====
    EvalCase(
        id="T03",
        query="查询跳舞的告警",
        expect_tools=["direct_response"],
        must_be_direct_response=True,
        tag="防幻觉-不支持类型",
    ),
    EvalCase(
        id="T04",
        query="查询唱歌的告警",
        expect_tools=["direct_response"],
        must_be_direct_response=True,
        tag="防幻觉-不支持类型",
    ),

    # ===== 3. 平台支持但可能无数据（不可提前断定无数据，必须真查） =====
    EvalCase(
        id="T05",
        query="查询未戴口罩的告警",
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"event_type": "ET03004"}},  # 未戴口罩 ET03004
        tag="支持但可能无数据",
    ),

    # ===== 4. 最近N条 vs 统计全量（语义区分） =====
    EvalCase(
        id="T06",
        query="查最近 5 条 AI 告警",
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"pagesize": 5}},
        forbid_args_keys={0: ["time_start", "time_end"]},  # "最近N条"是数量限制非时间
        tag="最近N条",
    ),
    EvalCase(
        id="T07",
        query="查最近 10 条告警",
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"pagesize": 10}},
        forbid_args_keys={0: ["time_start"]},
        tag="最近N条",
    ),
    EvalCase(
        id="T08",
        query="统计每种告警类型数量",
        expect_tools=["aggregate_alarms"],  # 统计全量，非查N条
        tag="统计全量",
    ),

    # ===== 5. 时间解析（显式时间才加time_start/end，且年份须为当前年） =====
    EvalCase(
        id="T09",
        query="查今天的告警",
        expect_tools=["ai_event_list"],
        # 期望 args 含 time_start 且年份为当前年（动态检查）
        tag="时间解析-今天",
    ),
    EvalCase(
        id="T10",
        query="查最近 7 天的告警",
        expect_tools=["ai_event_list"],
        # 期望 args 含 time_start 且年份为当前年
        tag="时间解析-最近N天",
    ),

    # ===== 6. 统计+画图（多步编排） =====
    EvalCase(
        id="T11",
        query="统计每种告警类型数量并画柱状图",
        expect_tools=["aggregate_alarms", "visualize_alarms"],
        expect_args_contains={1: {"chart_type": "bar"}},  # 默认柱状图
        tag="统计画图-柱状图",
    ),
    EvalCase(
        id="T12",
        query="按类型统计画饼图",
        expect_tools=["aggregate_alarms", "visualize_alarms"],
        expect_args_contains={1: {"chart_type": "pie"}},
        tag="统计画图-饼图",
    ),
    EvalCase(
        id="T13",
        query="统计告警并生成折线图",
        expect_tools=["aggregate_alarms", "visualize_alarms"],
        expect_args_contains={1: {"chart_type": "line"}},
        tag="统计画图-折线图",
    ),

    # ===== 7. 复判回写（VLM子图+回写） =====
    EvalCase(
        id="T14",
        query="复判告警 abc-123-uuid 并回写状态",
        expect_tools=["vlm_judge_alarm", "update_alarm_status"],
        tag="复判回写",
    ),

    # ===== 8. 知识库（kb_regulation子图） =====
    EvalCase(
        id="T15",
        query="未戴安全帽违反哪些规定",
        expect_tools=["kb_regulation"],
        tag="知识库",
    ),
    EvalCase(
        id="T16",
        query="吸烟会被怎么处罚",
        expect_tools=["kb_regulation"],
        tag="知识库",
    ),

    # ===== 9. 闲聊 fast-path（pre_route分流） =====
    EvalCase(
        id="T17",
        query="你好",
        expect_tools=["direct_response"],
        tag="闲聊-fast-path",
    ),
    EvalCase(
        id="T18",
        query="谢谢",
        expect_tools=["direct_response"],
        tag="闲聊-fast-path",
    ),

    # ===== 10. 设备查询 =====
    EvalCase(
        id="T19",
        query="查 AI 摄像机列表",
        expect_tools=["video_device_list"],
        tag="设备查询",
    ),
    EvalCase(
        id="T20",
        query="查询视频设备",
        expect_tools=["video_device_list"],
        tag="设备查询",
    ),

    # ===== 11. 录像回溯 =====
    EvalCase(
        id="T21",
        query="调出告警 xyz-456-uuid 前后录像",
        expect_tools=["fetch_alarm_context"],
        tag="录像回溯",
    ),

    # ===== 12. 跨类型组合查询 =====
    # 注：planner 当前策略是拆为两个 ai_event_list（也合理，formatter 可汇总）
    EvalCase(
        id="T22",
        query="统计抽烟和未戴安全帽各有多少",
        expect_tools=["ai_event_list", "ai_event_list"],  # 当前策略：两次查询
        expect_args_contains={0: {"event_type": "ET03002"}, 1: {"event_type": "ET03007"}},
        tag="跨类型组合",
    ),

    # ===== 13. T3 Few-shot 发散意图（措辞区别于上方用例，专测三类混淆边界） =====
    # 这批用例随 T3「Few-shot 覆盖发散意图」补充，守护对比型 few-shot 的价值，
    # 防止后续提示词改动重新引入"统计=单步""不支持类型瞎猜编码"等混淆。
    EvalCase(
        id="T23",
        query="总共有多少条告警",  # 纯计数、未提画图 → 单步 aggregate（few-shot ①）
        expect_tools=["aggregate_alarms"],
        forbid_args_keys={0: ["time_start", "time_end"]},
        tag="发散-纯统计",
    ),
    EvalCase(
        id="T24",
        query="查跳广场舞的告警",  # 不在权威清单 → direct_response（few-shot ②）
        expect_tools=["direct_response"],
        must_be_direct_response=True,
        tag="发散-不支持类型",
    ),
    EvalCase(
        id="T25",
        query="有没有人打篮球的告警",  # 同上，口语化措辞
        expect_tools=["direct_response"],
        must_be_direct_response=True,
        tag="发散-不支持类型",
    ),
    EvalCase(
        id="T26",
        query="给我看最近20条告警",  # 口语"最近N条" → pagesize 限数量，不加时间（few-shot ①）
        expect_tools=["ai_event_list"],
        expect_args_contains={0: {"pagesize": 20}},
        forbid_args_keys={0: ["time_start", "time_end"]},
        tag="发散-最近N条",
    ),
]


def get_all_cases() -> list[EvalCase]:
    """获取全部评测用例"""
    return EVAL_CASES


def get_cases_by_tag(tag: str) -> list[EvalCase]:
    """按标签筛选用例"""
    return [c for c in EVAL_CASES if tag in c.tag]
