"""短期记忆（会话级）支撑模块。

目标
----
让主图在「同一 thread_id（= session_id）」内记住此前聊过什么：每轮结束把
[用户问题, 最终答案] 追加进 state['messages']（LangGraph add_messages 累积），
checkpointer 按 thread_id 自动持久化；下轮进入图时 state['messages'] 已含历史，
节点据此构建"对话上下文"喂给 LLM，实现多轮连贯。

为什么需要本模块（设计权衡）
--------------------------
1. checkpointer 单例：主图全局单例编译一次，checkpointer 也必须是进程内单例，
   否则每次新建会丢历史。这里用进程内 MemorySaver（重启清空，符合"短期记忆"
   语义；如需跨重启持久化，后续可换 SqliteSaver，对调用方透明）。

2. 历史必须"瘦身"后再喂 LLM，否则 token 爆炸 + 拖慢响应：
   - 滑动窗口：只取最近 MAX_HISTORY_TURNS 轮（一问一答=1 轮）；
   - 字符上限：拼出的上下文超过 MAX_HISTORY_CHARS 时从最旧侧截断；
   - 剥离 base64：历史答案里的内嵌图表（data:image/...;base64, 长串）替换为
     占位符，绝不让几十 KB 的图片字节进入下一轮 prompt。

3. 记忆只在"纯文本 Plan-Execute"链路注入。看图（VLM）链路按产品语义每次独立
   分析当前图，不混入历史（也避免历史里的图片占位符干扰视觉判断）。
"""
from __future__ import annotations

import re
from typing import List, Optional

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

# ===================== 记忆策略常量 =====================
# 一问一答 = 1 轮。注入 LLM 的历史最多取最近这么多轮。
MAX_HISTORY_TURNS = 10
# 拼接后的历史上下文字符上限（约 ≈ token×1.5，2400 字 ≈ 1.6k token 量级），超出从最旧侧截断。
MAX_HISTORY_CHARS = 2400
# 软上限：单会话累计轮数达到此值时，前端提示用户"新建会话"（仅提示，不强制阻断）。
SOFT_TURN_LIMIT = 15
# 单条历史消息预览的最大字符数（防止某条超长答案独占预算）。
PER_MESSAGE_CHARS = 600

# base64 图片/图表占位：把 data:image/...;base64,<长串> 压成占位符。
_BASE64_IMG_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+")
# markdown 内嵌图片 ![alt](data:image...) 整体替换。
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]+\)")


# ===================== checkpointer 单例 =====================
_checkpointer: Optional[MemorySaver] = None


def get_checkpointer() -> MemorySaver:
    """返回进程内唯一的 checkpointer（短期记忆存储）。

    用 MemorySaver：进程内存，重启清空 —— 正是"短期/会话级记忆"的语义。
    若将来要跨进程/重启保留，换成 SqliteSaver(conn) 即可，上层无需改动。
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
        logger.info("[Memory] 短期记忆 checkpointer 已初始化（MemorySaver，进程内）")
    return _checkpointer


# ===================== 历史清洗 / 拼接 =====================
def _strip_heavy(text: str) -> str:
    """剥离文本里的 base64 图片，换成轻量占位符，避免历史把图片字节带进下一轮。"""
    if not text:
        return ""
    text = _MD_IMG_RE.sub("【图表】", text)
    text = _BASE64_IMG_RE.sub("【图片数据已省略】", text)
    return text.strip()


def _msg_text(msg: BaseMessage) -> str:
    """从 BaseMessage 取纯文本内容（兼容 content 为 list 的多模态结构）。"""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict):
                # 多模态分块：只保留 text，丢弃 image_url 等
                if seg.get("type") == "text" or "text" in seg:
                    parts.append(str(seg.get("text", "")))
            else:
                parts.append(str(seg))
        content = " ".join(p for p in parts if p)
    return _strip_heavy(str(content or ""))


def build_history_context(messages: List[BaseMessage], *, exclude_last_human: bool = True) -> str:
    """把累计的 messages 渲染成喂给 LLM 的"对话历史"文本块。

    Args:
        messages: state['messages']，按时间顺序的 Human/AI 交替消息。
        exclude_last_human: 调用通常发生在"本轮用户消息已 append 进 messages"之后，
            最后一条就是当前问题本身，不应作为"历史"重复喂入 → 默认剔除末尾的 Human。

    Returns:
        多行字符串（含"用户:/助手:"前缀）；无历史时返回空串。
        已做：滑动窗口（最近 MAX_HISTORY_TURNS 轮）、单条截断、总字符上限。
    """
    msgs = list(messages or [])
    if exclude_last_human and msgs and isinstance(msgs[-1], HumanMessage):
        msgs = msgs[:-1]
    if not msgs:
        return ""

    # 滑动窗口：最近 N 轮 ≈ 最近 2N 条消息
    msgs = msgs[-(MAX_HISTORY_TURNS * 2):]

    # 逐条渲染（从新到旧累加，受总字符上限约束），最后反转回时间正序
    rendered: list[str] = []
    total = 0
    for msg in reversed(msgs):
        if isinstance(msg, HumanMessage):
            role = "用户"
        elif isinstance(msg, AIMessage):
            role = "助手"
        else:
            continue
        text = _msg_text(msg)
        if not text:
            continue
        if len(text) > PER_MESSAGE_CHARS:
            text = text[:PER_MESSAGE_CHARS] + "…（略）"
        line = f"{role}：{text}"
        if total + len(line) > MAX_HISTORY_CHARS:
            break
        rendered.append(line)
        total += len(line)

    if not rendered:
        return ""
    rendered.reverse()
    return "\n".join(rendered)


def history_prompt_block(messages: List[BaseMessage]) -> str:
    """构建可直接拼进 system/user prompt 的"历史上下文"段落；无历史返回空串。"""
    ctx = build_history_context(messages)
    if not ctx:
        return ""
    return (
        "\n\n# 对话历史（最近若干轮，仅供理解上下文/指代消解，"
        "不要重复其中内容，也不要把历史里的旧数据当作本轮结果）\n"
        f"{ctx}\n"
    )


def count_turns(messages: List[BaseMessage]) -> int:
    """统计已完成的对话轮数（以 AI 回复条数计，一问一答=1 轮）。"""
    return sum(1 for m in (messages or []) if isinstance(m, AIMessage))
