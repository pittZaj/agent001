"""能力边界护栏（Capability Guard）—— 区分"平台能做"与"平台暂不支持"。

背景（2026-07-09）：
  用户"查看门口今天9点后有人出现的录像"这类问句，把一个**平台暂不支持的子诉求**
  （按视频内容检索"有人出现"，需目标检测/行为分析算法）包裹在一个**合理诉求**
  （按摄像头+时间调录像）里。旧链路只处理了合理部分（返回9点的录像），却**静默丢弃**
  了非法子诉求，给用户造成"平台支持按内容检索、只是结果不准"的错觉——损害产品口碑。

设计原则（对齐 skills.event_types 权威字典 + graph.harness_guard 反馈控制思路）：
  1. 确定性分支，不靠 LLM 猜：纯 Python 关键词判定，零 token 负担。
  2. 两类处理：
     - 逻辑上不可能满足（查未来时刻录像）→ 硬拦截（planner 前置，省 ~7000 token 提示词）。
     - 平台暂不支持的检索维度（按内容"有人/有车"筛录像）→ 软降级：照常返回录像，
       但**显式告知**该检索维度暂不支持（formatter 追加说明）。
  3. 可回退：config.harness.capability_guard_enabled 总开关。
"""
from loguru import logger

# 录像/回放意图关键词（能力边界仅约束"调录像"，不波及告警/统计查询）
_RECORD_KEYWORDS = ("录像", "回放", "录播", "录制")

# 明确指向未来的时间词（不可能有录像）。这些词单独出现即判未来；
# 与"今天9点后"（present）区分：present/past 锚点存在时一律放行。
_FUTURE_TOKENS = ("明天", "后天", "大后天", "未来", "下周", "下星期", "下个月", "下月", "明年")
_PRESENT_PAST_ANCHORS = ("今天", "昨天", "前天", "现在", "刚才", "刚刚")

# 需按"视频内容"检索的诉求关键词（目标检测/行为分析，平台暂不支持按内容筛录像片段）
_CONTENT_MARKERS = (
    "有人", "没人", "无人", "有没有人", "是否有人", "出现的人", "有人出现",
    "人出现", "出现人", "有车", "车辆", "陌生人", "可疑", "异常",
    "打架", "摔倒", "跌倒", "抽烟", "吸烟", "打电话", "玩手机",
    "安全帽", "徘徊", "入侵", "离岗", "聚集", "越界",
)


def is_capability_guard_enabled(config: dict) -> bool:
    """能力边界护栏总开关（默认启用，等价关闭时回退旧行为）。"""
    return config.get("harness", {}).get("capability_guard_enabled", True)


def _is_record_intent(msg: str) -> bool:
    return any(k in msg for k in _RECORD_KEYWORDS)


def check_future_record(user_message: str) -> str | None:
    """录像意图 + 明确指向未来 → 返回拦截文案（硬拦截）；否则返回 None（放行）。

    逻辑：录像是"已发生画面的存档"，查未来时刻录像逻辑上不可能满足。
    但"今天9点后""昨天15点"是 present/past，绝不能误拦——只要出现 present/past
    锚点就一律放行（保守优先，宁可漏拦也不误伤已验证可用的场景）。

    放在 planner **前置**，命中即 direct_response，省去 ~7000 token 大提示词构建。
    """
    msg = (user_message or "").strip()
    if not _is_record_intent(msg):
        return None
    # present/past 锚点存在 → 必是"今天/昨天"类查询，放行（含"今天9点后天黑前"这类）
    if any(a in msg for a in _PRESENT_PAST_ANCHORS):
        return None
    if not any(t in msg for t in _FUTURE_TOKENS):
        return None
    logger.info(f"[CapabilityGuard] 拦截未来时刻录像查询: {msg[:50]}")
    return (
        "抱歉，无法调取**未来时间**的录像。\n\n"
        "录像是已发生画面的存档记录，只能查询**当前或过去**某个时间点的录像"
        "（如「今天9点」「昨天15点」）。请提供一个已过去的时间点后重试。"
    )


def content_retrieval_notice(user_message: str) -> str | None:
    """录像意图 + 按视频内容检索（有人/有车/抽烟…）→ 返回"暂不支持"说明；否则 None。

    软降级策略：系统仍照常返回该摄像头对应时间点的录像，但**显式告知**当前不支持
    按视频内容（是否有人、是否有车、是否有某行为）筛选录像片段——该能力依赖目标检测/
    行为分析算法，属后续版本规划。避免用户误以为"平台已按内容筛选、只是结果不准"。

    仅在 video 录像 formatter 里调用，天然与告警查询链路隔离。
    """
    msg = (user_message or "").strip()
    if not _is_record_intent(msg):
        return None
    if not any(m in msg for m in _CONTENT_MARKERS):
        return None
    logger.info(f"[CapabilityGuard] 录像按内容检索暂不支持，追加说明: {msg[:50]}")
    return (
        "⚠️ **能力说明**：当前仅支持按**摄像头 + 时间点**调取录像，"
        "暂不支持按**画面内容**（如是否有人出现、是否有车、是否有特定行为）"
        "自动筛选录像片段。此能力依赖额外的视频内容分析算法，正在规划中，"
        "后续版本将开放。\n\n以下为该摄像头对应时间点的录像，请自行查看画面内容："
    )
