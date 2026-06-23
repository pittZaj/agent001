"""Redis-based Checkpointer for LangGraph

基于 Redis 的持久化 checkpointer，支持：
1. 跨进程/重启持久化会话状态
2. 自动过期清理（TTL）
3. 线程安全
4. 预留长期记忆接口

设计原则（遵循 Karpathy Guidelines）：
- 外科手术式修改：只改存储后端，接口保持兼容
- 保持简单：不过度设计，满足当前需求即可
- 预留扩展：为长期记忆预留命名空间
"""
from __future__ import annotations

import json
import pickle
from typing import Any, Dict, Optional, Sequence, Tuple
from contextlib import contextmanager

import redis
from langchain_core.runnables import RunnableConfig, ConfigurableFieldSpec
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple
from loguru import logger


class RedisCheckpointSaver(BaseCheckpointSaver):
    """基于 Redis 的 Checkpointer，实现会话状态持久化

    存储结构:
        key: {prefix}:{thread_id}:{checkpoint_ns}
        value: pickle(Checkpoint)
        ttl: 配置的过期时间（默认 30 天）

    特性:
        - 重启后记忆不丢失
        - 自动过期清理
        - 线程安全（Redis 自带）
        - 支持多租户隔离（通过 thread_id）
    """

    # 定义 LangGraph 需要的 config_specs
    config_specs = [
        ConfigurableFieldSpec(id="thread_id", annotation=str, name="Thread ID"),
    ]

    def __init__(
        self,
        redis_client: redis.Redis,
        key_prefix: str = "ksagent:memory:",
        ttl: int = 2592000,  # 30 天
    ):
        """初始化 Redis Checkpointer

        Args:
            redis_client: Redis 客户端实例
            key_prefix: Redis key 前缀
            ttl: 过期时间（秒），默认 30 天
        """
        super().__init__()
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.ttl = ttl
        logger.info(
            f"[Memory] RedisCheckpointSaver 初始化完成 "
            f"(prefix={key_prefix}, ttl={ttl}s)"
        )

    def _make_key(self, thread_id: str, checkpoint_ns: str = "") -> str:
        """生成 Redis key"""
        if checkpoint_ns:
            return f"{self.key_prefix}{thread_id}:{checkpoint_ns}"
        return f"{self.key_prefix}{thread_id}"

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> RunnableConfig:
        """保存 checkpoint 到 Redis

        Args:
            config: 配置（含 thread_id）
            checkpoint: 检查点数据
            metadata: 元数据
            new_versions: 通道版本信息（LangGraph 内部使用）

        Returns:
            更新后的配置
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        # 确保 metadata 包含必要的字段
        if not isinstance(metadata, dict):
            metadata = {}

        # LangGraph 需要的默认字段
        if "step" not in metadata:
            metadata["step"] = 0
        if "source" not in metadata:
            metadata["source"] = "update"
        if "writes" not in metadata:
            metadata["writes"] = {}

        # 序列化 checkpoint（包括 new_versions）
        data = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions,
        }
        key = self._make_key(thread_id, checkpoint_ns)

        try:
            # 使用 pickle 序列化（兼容 LangGraph 的复杂对象）
            value = pickle.dumps(data)
            # 使用 set 替代 setex（兼容旧版本 Redis）
            self.redis.set(key, value, ex=self.ttl)
            logger.debug(f"[Memory] Checkpoint 已保存: {key}")
        except Exception as e:
            logger.error(f"[Memory] Checkpoint 保存失败: {e}")
            raise

        return config

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """保存写入操作（LangGraph 内部使用）

        注意：当前实现为简化版，直接忽略 writes
        因为我们的 checkpoint 策略是覆盖写，不需要增量写入
        """
        # 简化实现：不需要单独保存 writes
        # 完整的 checkpoint 已经包含所有状态
        pass

    def get(self, config: RunnableConfig) -> Optional[Checkpoint]:
        """从 Redis 加载 checkpoint

        Args:
            config: 配置（含 thread_id）

        Returns:
            Checkpoint 或 None
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        key = self._make_key(thread_id, checkpoint_ns)

        try:
            value = self.redis.get(key)
            if not value:
                return None

            data = pickle.loads(value)
            logger.debug(f"[Memory] Checkpoint 已加载: {key}")
            return data["checkpoint"]
        except Exception as e:
            logger.warning(f"[Memory] Checkpoint 加载失败: {e}")
            return None

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """获取 checkpoint tuple（LangGraph 需要的方法）

        Args:
            config: 配置（含 thread_id）

        Returns:
            CheckpointTuple 或 None
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        key = self._make_key(thread_id, checkpoint_ns)

        try:
            value = self.redis.get(key)
            if not value:
                return None

            data = pickle.loads(value)
            metadata = data.get("metadata", {})

            # 确保 metadata 包含 LangGraph 需要的字段
            if not isinstance(metadata, dict):
                metadata = {}
            if "step" not in metadata:
                metadata["step"] = 0
            if "source" not in metadata:
                metadata["source"] = "update"
            if "writes" not in metadata:
                metadata["writes"] = {}

            logger.debug(f"[Memory] Checkpoint tuple 已加载: {key}")
            return CheckpointTuple(
                config=config,
                checkpoint=data["checkpoint"],
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"[Memory] Checkpoint tuple 加载失败: {e}")
            return None

    def list(
        self,
        config: RunnableConfig,
        *,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Sequence[CheckpointTuple]:
        """列出 checkpoints（简化实现，返回当前最新的）

        Note: Redis 存储模式下，每个 thread_id 只保留最新的 checkpoint
        """
        checkpoint = self.get(config)
        if checkpoint is None:
            return []

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        try:
            key = self._make_key(thread_id, checkpoint_ns)
            value = self.redis.get(key)
            if not value:
                return []

            data = pickle.loads(value)
            return [
                CheckpointTuple(
                    config=config,
                    checkpoint=data["checkpoint"],
                    metadata=data["metadata"],
                )
            ]
        except Exception as e:
            logger.warning(f"[Memory] Checkpoint 列表获取失败: {e}")
            return []

    def delete(self, thread_id: str, checkpoint_ns: str = "") -> bool:
        """删除指定会话的全部 Redis 数据（checkpoint、命名空间、:meta 元数据）。

        LangGraph 可能写入：
          - {prefix}{thread_id}
          - {prefix}{thread_id}:{checkpoint_ns}
          - {prefix}{thread_id}:meta  （会话标题，新建/重命名时写入）
        仅删主 key 会导致列表仍能看到该会话。
        """
        if not thread_id:
            return False
        try:
            # 兼容旧调用：若指定了 checkpoint_ns，仍只删该 key
            if checkpoint_ns:
                key = self._make_key(thread_id, checkpoint_ns)
                result = self.redis.delete(key)
                logger.info(f"[Memory] Checkpoint 已删除: {key}")
                return result > 0

            pattern = f"{self.key_prefix}{thread_id}*"
            keys = self.redis.keys(pattern)
            if not keys:
                logger.warning(f"[Memory] 未找到会话 key: {pattern}")
                return False
            deleted = self.redis.delete(*keys)
            logger.info(
                f"[Memory] 会话 {thread_id} 已删除 {deleted} 个 key "
                f"({len(keys)} 个匹配)"
            )
            return deleted > 0
        except Exception as e:
            logger.error(f"[Memory] Checkpoint 删除失败: {e}")
            return False

    def list_threads(self, pattern: str = "*") -> list[str]:
        """列出所有会话 ID（用于会话管理）

        Args:
            pattern: 匹配模式

        Returns:
            会话 ID 列表
        """
        try:
            pattern_key = f"{self.key_prefix}{pattern}"
            keys = self.redis.keys(pattern_key)
            # 提取 thread_id
            threads = []
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                # 移除前缀，提取 thread_id
                if key_str.startswith(self.key_prefix):
                    thread_part = key_str[len(self.key_prefix):]
                    # 如果有命名空间，取第一段
                    thread_id = thread_part.split(":", 1)[0]
                    if thread_id not in threads:
                        threads.append(thread_id)
            return threads
        except Exception as e:
            logger.error(f"[Memory] 会话列表获取失败: {e}")
            return []

    def get_thread_metadata(self, thread_id: str) -> Optional[dict]:
        """获取会话元数据

        Args:
            thread_id: 会话 ID

        Returns:
            元数据字典或 None
        """
        key = self._make_key(thread_id)
        try:
            value = self.redis.get(key)
            if not value:
                return None

            data = pickle.loads(value)
            checkpoint = data["checkpoint"]

            # 提取有用的元数据
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            return {
                "thread_id": thread_id,
                "message_count": len(messages),
                "last_update": data.get("metadata", {}).get("timestamp"),
            }
        except Exception as e:
            logger.warning(f"[Memory] 会话元数据获取失败: {e}")
            return None


# ===================== 长期记忆接口预留 =====================

class LongTermMemoryInterface:
    """长期记忆接口（预留，未来扩展）

    设计思路:
        - 存储：独立 Redis 命名空间或 SQLite 表
        - 内容：用户偏好、常用查询模板、领域知识
        - 检索：向量化 + 相似度搜索
        - 注入：planner 启动时作为额外上下文

    使用场景:
        - 用户偏好：如常查询某类告警、关注特定摄像头
        - 查询模板：用户常用的查询模式
        - 领域知识：跨会话积累的业务规则

    实现时机:
        - 当客户反馈需要跨会话记忆时再实施
        - 预计工作量：3~5 人日
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "ksagent:longterm:"):
        self.redis = redis_client
        self.key_prefix = key_prefix
        logger.info("[Memory] LongTermMemory 接口已预留（未启用）")

    def store_preference(self, user_id: str, preference: dict) -> bool:
        """存储用户偏好（预留接口）"""
        raise NotImplementedError("长期记忆功能未启用，等待客户反馈后实施")

    def get_preferences(self, user_id: str) -> Optional[dict]:
        """获取用户偏好（预留接口）"""
        raise NotImplementedError("长期记忆功能未启用，等待客户反馈后实施")

    def store_query_template(self, user_id: str, template: dict) -> bool:
        """存储查询模板（预留接口）"""
        raise NotImplementedError("长期记忆功能未启用，等待客户反馈后实施")
