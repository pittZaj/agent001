"""MCP 后台连接监听器：启动不阻塞，断线自动重连，上线后注册工具。"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from loguru import logger

from utils import CONFIG
from utils.async_loop import get_background_loop

if TYPE_CHECKING:
    from skills.registry import SkillRegistry

_stop_flag = threading.Event()
_watcher_started = False
_mcp_online = False
_lock = threading.Lock()


def is_mcp_online() -> bool:
    return _mcp_online


def _set_online(value: bool) -> None:
    global _mcp_online
    _mcp_online = value


def _reconnect_config() -> dict:
    cfg = CONFIG.get("mcp") or {}
    return cfg.get("reconnect") or {}


async def _try_connect(registry: SkillRegistry, connect_timeout: float) -> bool:
    from mcp_adapter.client import get_mcp_client
    from skills.mcp_skills import register_mcp_skills

    client = await asyncio.wait_for(
        get_mcp_client(force_reconnect=True),
        timeout=connect_timeout,
    )
    if not client.enabled or not client.list_servers():
        return False
    registry.set_mcp_client(client)
    await register_mcp_skills(registry, client)
    return True


async def _health_check() -> bool:
    from mcp_adapter.client import get_mcp_client

    try:
        client = await get_mcp_client()
        servers = client.list_servers()
        if not servers:
            return False
        await client.list_tools(servers[0])
        return True
    except Exception:
        return False


async def _go_offline(registry: SkillRegistry) -> None:
    from mcp_adapter.client import reset_mcp_client
    from skills.mcp_skills import unregister_mcp_skills

    _set_online(False)
    unregister_mcp_skills(registry)
    try:
        await reset_mcp_client()
    except Exception as exc:
        logger.warning(f"[MCP Watcher] 重置 MCP Client 失败: {exc}")
    logger.warning("[MCP Watcher] MCP 已下线，等待重连...")


async def _watcher_loop(registry: SkillRegistry) -> None:
    mcp_cfg = CONFIG.get("mcp") or {}
    if not mcp_cfg.get("enabled", False):
        logger.info("[MCP Watcher] MCP 未启用，跳过监听")
        return

    rcfg = _reconnect_config()
    interval = float(rcfg.get("interval", 10))
    max_interval = float(rcfg.get("max_interval", 60))
    health_interval = float(rcfg.get("health_interval", 30))
    connect_timeout = float(rcfg.get("connect_timeout", 10))
    backoff = interval

    endpoint = mcp_cfg.get("endpoint", "")
    logger.info(
        f"[MCP Watcher] 启动，目标={endpoint}，"
        f"重试间隔={interval}s，连接超时={connect_timeout}s"
    )

    while not _stop_flag.is_set():
        if is_mcp_online():
            if not await _health_check():
                await _go_offline(registry)
                backoff = interval
            else:
                await _sleep_interruptible(health_interval)
            continue

        try:
            if await _try_connect(registry, connect_timeout):
                _set_online(True)
                backoff = interval
                n_mcp = sum(
                    1 for s in registry.list_skills() if s.skill_type.value == "mcp_tool"
                )
                logger.info(f"[MCP Watcher] MCP 已上线，已注册 {n_mcp} 个工具")
                continue
        except asyncio.TimeoutError:
            logger.warning(f"[MCP Watcher] 连接超时（>{connect_timeout}s）")
        except Exception as exc:
            logger.warning(f"[MCP Watcher] 连接失败: {exc}")

        logger.info(f"[MCP Watcher] {backoff:.0f}s 后重试...")
        await _sleep_interruptible(backoff)
        backoff = min(backoff * 1.5, max_interval)

    logger.info("[MCP Watcher] 已停止")


async def _sleep_interruptible(seconds: float) -> None:
    """可中断 sleep，便于 shutdown 时快速退出。"""
    slept = 0.0
    while slept < seconds and not _stop_flag.is_set():
        chunk = min(1.0, seconds - slept)
        await asyncio.sleep(chunk)
        slept += chunk


def start_mcp_watcher(registry: SkillRegistry | None = None) -> None:
    """在后台事件循环上启动 MCP 监听（幂等）。"""
    global _watcher_started
    from skills import get_skill_registry

    reg = registry or get_skill_registry()
    with _lock:
        if _watcher_started:
            return
        _stop_flag.clear()
        loop = get_background_loop()
        asyncio.run_coroutine_threadsafe(_watcher_loop(reg), loop)
        _watcher_started = True


def stop_mcp_watcher() -> None:
    """通知监听器停止（进程退出时调用）。"""
    global _watcher_started
    with _lock:
        _stop_flag.set()
        _watcher_started = False
