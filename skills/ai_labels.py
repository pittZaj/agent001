"""AI 视觉告警字段展示映射（与 KSIpms 前端 dict/ai.js 一致）。"""

LEVEL_LABEL: dict[str, str] = {
    "red": "特别重大",
    "orange": "重大",
    "yellow": "较大",
    "blue": "一般",
}

# 复核结果（review_status），与 eventReviewStatusLabel / 事件列表 alarmType 一致
REVIEW_STATUS_LABEL: dict[int, str] = {
    1: "复核误报",
    2: "复核告警",
    3: "未复核",
    4: "已复核",
}

# 事件流程处理状态（status 英文键），与 eventWorkflowStatusLabel 一致
WORKFLOW_STATUS_LABEL: dict[str, str] = {
    "unconfirmed": "未确认",
    "confirmed": "已确认",
    "finished": "已完成",
    "misinformation": "误报",
    "ignore": "已忽略",
}


def level_label(level: str | None) -> str:
    if not level:
        return "未知"
    key = str(level).strip().lower()
    return LEVEL_LABEL.get(key, str(level))


def review_status_label(review_status) -> str:
    if review_status is None or review_status == "":
        return "—"
    try:
        key = int(review_status)
    except (TypeError, ValueError):
        return str(review_status)
    return REVIEW_STATUS_LABEL.get(key, str(review_status))


def workflow_status_label(status) -> str:
    if status is None or status == "":
        return "—"
    key = str(status).strip().lower()
    return WORKFLOW_STATUS_LABEL.get(key, str(status))
