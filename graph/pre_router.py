"""预路由模块：在 planner 之前做轻量分流，跳过大提示词

设计原则（遵循 Karpathy Guidelines）：
1. 规则极度保守，宁可漏判也不误判（误判会破坏用户体验）
2. 闲聊用严格正则（全字匹配），避免"你好，查询告警"被误判
3. 知识库需同时满足：有 KB 关键词 + 无告警关键词
4. 录像/告警/设备等复杂意图统一走 Planner，由大模型解析
5. 未命中时无任何副作用，完全走原 planner 逻辑
"""
import re
from loguru import logger


# 纯闲聊模式（正则严格匹配，避免误判）
CHITCHAT_PATTERNS = [
    r'^(你好|您好|hi|hello|嗨|hey)[\s！!。.？?]*$',
    r'^(谢谢|多谢|感谢|thanks|thx)[\s！!。.]*$',
    r'^(再见|拜拜|bye|goodbye)[\s！!。.]*$',
    r'^(好的|好|可以|ok|okay)[\s！!。.]*$',
]

# 知识库关键词（包含任一即判定为知识库问题的候选）
KB_KEYWORDS = [
    '规章', '制度', '规定', '条例', '标准', '规范', '处罚', '违规',
    '安全生产法', '劳动法', '管理办法', '操作规程', '应急预案',
]

# 业务关键词（用于排除知识库误判）
BUSINESS_KEYWORDS = [
    '告警', '摄像头', '设备', '复判', '统计', '查询',
    '监控', '视频', '录像', '画图', '柱状图', '饼图', '折线图',
]


def pre_route(user_message: str) -> dict | None:
    """预路由判断，返回 None 表示需走完整 planner

    Args:
        user_message: 用户输入消息

    Returns:
        None: 需走完整 planner
        dict: 直接路由结果 {"route": "direct_response"|"kb", "plan": [...]}
    """
    msg = user_message.strip()

    # 1. 纯闲聊（正则严格匹配，避免误判）
    for pattern in CHITCHAT_PATTERNS:
        if re.match(pattern, msg, re.IGNORECASE):
            logger.info(f"[PreRouter] 命中闲聊规则: {pattern}")
            return {
                "route": "direct_response",
                "plan": [{
                    "task": "direct_response",
                    "args": {"text": _get_chitchat_response(msg)},
                    "status": "pending"
                }],
            }

    # 2. 纯知识库问题（包含关键词 + 不含告警/摄像头等业务词）
    has_kb_keyword = any(kw in msg for kw in KB_KEYWORDS)
    has_business_keyword = any(word in msg for word in BUSINESS_KEYWORDS)

    if has_kb_keyword and not has_business_keyword:
        logger.info(f"[PreRouter] 命中知识库规则")
        return {
            "route": "kb",
            "plan": [{
                "task": "kb_regulation",
                "args": {"query": user_message},
                "status": "pending"
            }],
        }

    # 3. 其他情况走完整 planner（录像/告警/设备等由大模型解析）
    return None


def _get_chitchat_response(msg: str) -> str:
    """闲聊固定回复"""
    msg_lower = msg.lower()
    if any(w in msg_lower for w in ['你好', 'hello', 'hi', '您好', '嗨']):
        return "你好！我是 KSIpms 智能助手，可以帮你查询告警、统计分析、检索规章制度等。有什么可以帮到你的吗？"
    elif any(w in msg_lower for w in ['谢谢', 'thanks', '感谢', '多谢']):
        return "不客气！有其他问题随时问我。"
    elif any(w in msg_lower for w in ['再见', 'bye', '拜拜']):
        return "再见！祝工作顺利！"
    else:
        return "好的！"
