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
_checkpointer = None


def get_checkpointer():
    """返回持久化的 checkpointer（短期记忆存储）

    存储策略：
    - Redis：持久化存储，重启后记忆不丢失（生产环境）
    - MemorySaver：进程内存，重启清空（降级/测试环境）

    切换逻辑：
    - 优先使用 Redis（如果配置启用且连接成功）
    - 失败时降级到 MemorySaver
    """
    global _checkpointer
    if _checkpointer is None:
        from utils import CONFIG

        # 尝试使用 Redis
        redis_config = CONFIG.get("redis", {})
        if redis_config.get("enabled", False):
            try:
                import redis
                from graph.redis_checkpoint import RedisCheckpointSaver

                # 创建 Redis 客户端
                host, port = redis_config["addr"].split(":")
                redis_client = redis.Redis(
                    host=host,
                    port=int(port),
                    password=redis_config.get("password") or None,
                    db=redis_config.get("db", 0),
                    decode_responses=False,  # 使用 bytes 模式，兼容 pickle
                    protocol=2,  # 使用 RESP2 协议，兼容旧版本 Redis
                )

                # 测试连接
                redis_client.ping()

                # 创建 Redis checkpointer
                _checkpointer = RedisCheckpointSaver(
                    redis_client=redis_client,
                    key_prefix=redis_config.get("key_prefix", "ksagent:memory:"),
                    ttl=redis_config.get("ttl", 2592000),  # 30 天
                )
                logger.info("[Memory] 使用 Redis 持久化存储（重启后记忆保留）")
                return _checkpointer

            except Exception as e:
                logger.warning(f"[Memory] Redis 连接失败，降级到 MemorySaver: {e}")

        # 降级到 MemorySaver
        _checkpointer = MemorySaver()
        logger.info("[Memory] 使用 MemorySaver（进程内存，重启清空）")

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


def build_history_context(
    messages: List[BaseMessage],
    *,
    exclude_last_human: bool = True,
    max_turns: int | None = None,
    max_chars: int | None = None,
    per_message_chars: int | None = None,
) -> str:
    """把累计的 messages 渲染成喂给 LLM 的"对话历史"文本块（兼容旧接口）"""
    return build_history_context_with_summary(
        messages,
        exclude_last_human=exclude_last_human,
        use_summary=False,
        max_turns=max_turns,
        max_chars=max_chars,
        per_message_chars=per_message_chars,
    )


def history_prompt_block(
    messages: List[BaseMessage],
    *,
    max_turns: int | None = None,
    max_chars: int | None = None,
    per_message_chars: int | None = None,
) -> str:
    """构建可直接拼进 prompt 的历史段落；无历史返回空串。

    planner 等 token 紧张场景可传入更小的 max_turns / max_chars。
    """
    ctx = build_history_context(
        messages,
        max_turns=max_turns,
        max_chars=max_chars,
        per_message_chars=per_message_chars,
    )
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


# ===================== 智能摘要（替代硬截断） =====================

async def _summarize_history_async(messages: List[BaseMessage], max_summary_length: int = 200) -> str:
    """用 LLM 将历史对话压缩成摘要（保留关键上下文）

    Args:
        messages: 需要摘要的历史消息
        max_summary_length: 摘要最大长度

    Returns:
        摘要文本
    """
    if not messages:
        return ""

    # 构建摘要提示词
    history_text = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = "用户"
        elif isinstance(msg, AIMessage):
            role = "助手"
        else:
            continue
        text = _msg_text(msg)
        if text:
            history_text.append(f"{role}：{text[:200]}")

    if not history_text:
        return ""

    history_str = "\n".join(history_text)

    # 使用 LLM 生成摘要
    try:
        from utils.llm_pool import get_llm

        llm = get_llm(role="summarizer", temperature=0.1)

        prompt = f"""请将以下对话历史压缩成简洁的摘要（不超过{max_summary_length}字），保留关键信息：

{history_str}

要求：
1. 提取用户的核心需求和关键约束条件
2. 保留重要的查询参数（如时间范围、告警类型等）
3. 忽略闲聊和重复内容
4. 用第三人称客观描述

摘要："""

        from langchain_core.messages import HumanMessage as LCHumanMessage
        response = await llm.ainvoke([LCHumanMessage(content=prompt)])
        summary = response.content.strip()

        logger.info(f"[Memory] 历史摘要生成：{len(history_text)} 条消息 → {len(summary)} 字")
        return summary

    except Exception as e:
        logger.warning(f"[Memory] 历史摘要失败，使用硬截断: {e}")
        # 降级：返回简单拼接
        return "（早期对话）" + " / ".join(history_text[:3])


def build_history_context_with_summary(
    messages: List[BaseMessage],
    *,
    exclude_last_human: bool = True,
    use_summary: bool = True,
    max_turns: int | None = None,
    max_chars: int | None = None,
    per_message_chars: int | None = None,
) -> str:
    """构建历史上下文，支持智能摘要

    Args:
        messages: state['messages']
        exclude_last_human: 是否排除最后一条用户消息
        use_summary: 是否启用智能摘要（超长时自动触发）

    Returns:
        历史上下文文本
    """
    msgs = list(messages or [])
    if exclude_last_human and msgs and isinstance(msgs[-1], HumanMessage):
        msgs = msgs[:-1]
    if not msgs:
        return ""

    # 判断是否需要摘要
    total_chars = sum(len(_msg_text(m)) for m in msgs)
    need_summary = use_summary and total_chars > MAX_HISTORY_CHARS * 2

    if need_summary and len(msgs) > MAX_HISTORY_TURNS * 2:
        # 历史过长，分为两部分：
        # 1. 早期部分：生成摘要
        # 2. 最近部分：保留原文
        split_point = len(msgs) - (MAX_HISTORY_TURNS * 2)
        early_msgs = msgs[:split_point]
        recent_msgs = msgs[split_point:]

        # 注意：这里是同步函数，但 LLM 调用是异步的
        # 实际使用时，应该在异步上下文中调用或缓存摘要结果
        # 为了简化，这里先使用硬截断，摘要功能作为可选增强

        logger.info(
            f"[Memory] 历史过长（{len(msgs)} 条消息，{total_chars} 字），"
            f"使用滑动窗口（保留最近 {len(recent_msgs)} 条）"
        )

        # TODO: 集成异步摘要（需要在 planner_node 等异步上下文中调用）
        # summary = await _summarize_history_async(early_msgs)
        # 当前降级方案：只保留最近的消息
        msgs = recent_msgs

    # 使用原有逻辑构建上下文
    return _build_context_from_messages(
        msgs,
        max_turns=max_turns or MAX_HISTORY_TURNS,
        max_chars=max_chars or MAX_HISTORY_CHARS,
        per_message_chars=per_message_chars or PER_MESSAGE_CHARS,
    )


def _build_context_from_messages(
    msgs: List[BaseMessage],
    *,
    max_turns: int = MAX_HISTORY_TURNS,
    max_chars: int = MAX_HISTORY_CHARS,
    per_message_chars: int = PER_MESSAGE_CHARS,
) -> str:
    """从消息列表构建上下文文本（内部辅助函数）"""
    # 滑动窗口：最近 N 轮 ≈ 最近 2N 条消息
    msgs = msgs[-(max_turns * 2):]

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
        if len(text) > per_message_chars:
            text = text[:per_message_chars] + "…（略）"
        line = f"{role}：{text}"
        if total + len(line) > max_chars:
            break
        rendered.append(line)
        total += len(line)

    if not rendered:
        return ""
    rendered.reverse()
    return "\n".join(rendered)
