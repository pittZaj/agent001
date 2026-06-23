"""会话管理工具模块

提供会话的增删改查、导出等功能，供 Web 界面调用。

功能：
1. 列出所有会话
2. 删除会话
3. 重命名会话（存储在元数据中）
4. 导出会话为 Markdown/JSON
5. 获取会话统计信息
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import json

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from loguru import logger

from graph.memory import get_checkpointer, _msg_text


class SessionManager:
    """会话管理器"""

    def __init__(self):
        self._checkpointer = None
        self._checkpointer_type = None
        logger.debug("[SessionManager] 初始化完成（延迟加载 checkpointer）")

    @property
    def checkpointer(self):
        """延迟加载 checkpointer（确保主图已初始化）"""
        if self._checkpointer is None:
            self._checkpointer = get_checkpointer()
            self._checkpointer_type = type(self._checkpointer).__name__
            logger.debug(f"[SessionManager] Checkpointer 已加载，类型: {self._checkpointer_type}")
        return self._checkpointer

    def _ensure_redis_checkpointer(self) -> bool:
        """确保 checkpointer 是 RedisCheckpointSaver"""
        # 强制重新获取 checkpointer（防止缓存的是 MemorySaver）
        self._checkpointer = get_checkpointer()
        self._checkpointer_type = type(self._checkpointer).__name__

        if self._checkpointer_type != "RedisCheckpointSaver":
            logger.debug(
                f"[SessionManager] Checkpointer 类型: {self._checkpointer_type}（非 RedisCheckpointSaver）"
            )

        return self._checkpointer_type == "RedisCheckpointSaver"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话

        Returns:
            会话列表，每个会话包含：
            - thread_id: 会话 ID
            - name: 会话名称（如果有）
            - message_count: 消息数量
            - last_update: 最后更新时间
            - turn_count: 对话轮数
        """
        try:
            # 确保使用 RedisCheckpointSaver
            if not self._ensure_redis_checkpointer():
                logger.warning(
                    f"[SessionManager] 当前 checkpointer ({self._checkpointer_type}) 不支持会话列表。"
                    f"请确保 Redis 已启用并正常连接。"
                )
                return []

            # 尝试使用 Redis checkpointer 的扩展方法
            if hasattr(self.checkpointer, "list_threads") and callable(getattr(self.checkpointer, "list_threads")):
                thread_ids = self.checkpointer.list_threads()
                sessions = []
                for thread_id in thread_ids:
                    metadata = self._get_session_metadata(thread_id)
                    if metadata:
                        sessions.append(metadata)
                logger.debug(f"[SessionManager] 列出 {len(sessions)} 个会话")
                return sessions
            else:
                logger.warning("[SessionManager] RedisCheckpointSaver 缺少 list_threads 方法")
                return []
        except Exception as e:
            logger.error(f"[SessionManager] 列出会话失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def delete_session(self, thread_id: str) -> bool:
        """删除会话

        Args:
            thread_id: 会话 ID

        Returns:
            是否删除成功
        """
        try:
            # 确保使用 RedisCheckpointSaver
            if not self._ensure_redis_checkpointer():
                logger.warning(
                    f"[SessionManager] 当前 checkpointer ({self._checkpointer_type}) 不支持删除。"
                    f"请确保 Redis 已启用并正常连接。"
                )
                return False

            # 检查是否有 delete 方法
            if hasattr(self.checkpointer, "delete") and callable(getattr(self.checkpointer, "delete")):
                result = self.checkpointer.delete(thread_id)
                if result:
                    logger.info(f"[SessionManager] 会话 {thread_id} 已删除")
                else:
                    logger.warning(f"[SessionManager] 会话 {thread_id} 删除未命中任何 Redis key")
                return result
            else:
                logger.warning("[SessionManager] RedisCheckpointSaver 缺少 delete 方法")
                return False
        except Exception as e:
            logger.error(f"[SessionManager] 删除会话失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def rename_session(self, thread_id: str, new_name: str) -> bool:
        """重命名会话（存储在 Redis 额外 key 中）

        Args:
            thread_id: 会话 ID
            new_name: 新名称

        Returns:
            是否重命名成功
        """
        try:
            # 确保使用 RedisCheckpointSaver
            if not self._ensure_redis_checkpointer():
                logger.warning(
                    f"[SessionManager] 当前 checkpointer ({self._checkpointer_type}) 不支持重命名。"
                    f"请确保 Redis 已启用并正常连接。"
                )
                return False

            # 检查是否有必要的属性
            if hasattr(self.checkpointer, "redis") and hasattr(self.checkpointer, "key_prefix"):
                # 存储在独立的元数据 key
                meta_key = f"{self.checkpointer.key_prefix}{thread_id}:meta"
                metadata = {"name": new_name, "updated_at": datetime.now().isoformat()}
                # 使用 set 替代 setex（兼容旧版本 Redis）
                self.checkpointer.redis.set(
                    meta_key,
                    json.dumps(metadata),
                    ex=self.checkpointer.ttl
                )
                logger.info(f"[SessionManager] 会话 {thread_id} 已重命名为 {new_name}")
                return True
            else:
                logger.warning("[SessionManager] RedisCheckpointSaver 缺少 redis 或 key_prefix 属性")
                return False
        except Exception as e:
            logger.error(f"[SessionManager] 重命名会话失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def export_session(
        self, thread_id: str, format: str = "markdown"
    ) -> Optional[str]:
        """导出会话为文本

        Args:
            thread_id: 会话 ID
            format: 导出格式（"markdown" 或 "json"）

        Returns:
            导出的文本内容或 None
        """
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = self.checkpointer.get(config)

            if not checkpoint:
                logger.warning(f"[SessionManager] 会话 {thread_id} 不存在")
                return None

            messages = checkpoint.get("channel_values", {}).get("messages", [])

            if format == "markdown":
                return self._export_as_markdown(thread_id, messages)
            elif format == "json":
                return self._export_as_json(thread_id, messages)
            else:
                logger.error(f"[SessionManager] 不支持的导出格式: {format}")
                return None

        except Exception as e:
            logger.error(f"[SessionManager] 导出会话失败: {e}")
            return None

    def get_session_stats(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """获取会话统计信息

        Args:
            thread_id: 会话 ID

        Returns:
            统计信息字典
        """
        return self._get_session_metadata(thread_id)

    def get_session_messages(self, thread_id: str) -> List[Dict[str, str]]:
        """获取会话消息列表（user/assistant 文本）。"""
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = self.checkpointer.get(config)
            if not checkpoint:
                return []

            messages = checkpoint.get("channel_values", {}).get("messages", [])
            history: List[Dict[str, str]] = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    text = _msg_text(msg)
                    if text:
                        history.append({"role": "user", "content": text})
                elif isinstance(msg, AIMessage):
                    text = str(msg.content or "").strip()
                    if text:
                        history.append({"role": "assistant", "content": text})
            return history
        except Exception as e:
            logger.error(f"[SessionManager] 获取会话消息失败: {e}")
            return []

    def list_sessions_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """按用户前缀过滤会话列表（sess_{user_id}_）。"""
        prefix = f"sess_{user_id}_"
        sessions = self.list_sessions()
        return [s for s in sessions if str(s.get("thread_id", "")).startswith(prefix)]

    @staticmethod
    def new_thread_id(user_id: str) -> str:
        import uuid

        safe_user = (user_id or "default").strip() or "default"
        return f"sess_{safe_user}_{uuid.uuid4().hex[:12]}"

    # ===================== 内部辅助方法 =====================

    def _get_session_metadata(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """获取会话元数据"""
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = self.checkpointer.get(config)

            if not checkpoint:
                return None

            messages = checkpoint.get("channel_values", {}).get("messages", [])

            # 获取自定义名称（如果有）
            name = None
            if hasattr(self.checkpointer, "redis"):
                meta_key = f"{self.checkpointer.key_prefix}{thread_id}:meta"
                meta_value = self.checkpointer.redis.get(meta_key)
                if meta_value:
                    meta_data = json.loads(meta_value)
                    name = meta_data.get("name")

            # 统计轮数
            turn_count = sum(1 for m in messages if isinstance(m, AIMessage))

            # 提取第一条用户消息作为默认名称
            default_name = None
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    text = _msg_text(msg)
                    if text:
                        default_name = text[:30] + ("..." if len(text) > 30 else "")
                        break

            return {
                "thread_id": thread_id,
                "name": name or default_name or f"会话 {thread_id[:8]}",
                "message_count": len(messages),
                "turn_count": turn_count,
                "last_update": None,  # TODO: 从 checkpoint 提取时间戳
            }

        except Exception as e:
            logger.warning(f"[SessionManager] 获取会话元数据失败: {e}")
            return None

    def _export_as_markdown(self, thread_id: str, messages: List[BaseMessage]) -> str:
        """导出为 Markdown 格式"""
        lines = [
            f"# 会话导出：{thread_id}",
            f"",
            f"**导出时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**消息数量**：{len(messages)} 条",
            f"",
            "---",
            "",
        ]

        for i, msg in enumerate(messages, 1):
            if isinstance(msg, HumanMessage):
                role = "👤 用户"
            elif isinstance(msg, AIMessage):
                role = "🤖 助手"
            else:
                continue

            text = _msg_text(msg)
            if not text:
                continue

            lines.append(f"### {i}. {role}")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _export_as_json(self, thread_id: str, messages: List[BaseMessage]) -> str:
        """导出为 JSON 格式"""
        data = {
            "thread_id": thread_id,
            "export_time": datetime.now().isoformat(),
            "message_count": len(messages),
            "messages": [],
        }

        for msg in messages:
            if isinstance(msg, HumanMessage):
                role = "user"
            elif isinstance(msg, AIMessage):
                role = "assistant"
            else:
                continue

            text = _msg_text(msg)
            if not text:
                continue

            data["messages"].append({"role": role, "content": text})

        return json.dumps(data, ensure_ascii=False, indent=2)


# ===================== 全局单例 =====================

_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取会话管理器单例"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
