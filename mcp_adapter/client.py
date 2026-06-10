import asyncio
import json
import threading
from contextlib import AsyncExitStack
from typing import Any, Dict, Optional

from loguru import logger

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from utils import CONFIG


class MCPClient:
    """MCP 客户端（同时支持 stdio 与 streamable HTTP）"""

    def __init__(self):
        self.config = CONFIG.get("mcp", {})
        self.enabled = self.config.get("enabled", False)
        # server_name -> {"session": ClientSession, "exit_stack": AsyncExitStack, "transport": str}
        self.servers: dict[str, dict] = {}

        if not self.enabled:
            logger.warning("MCP 未启用，所有工具调用走 mock")

    async def connect_stdio_server(
        self,
        server_name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        """通过 stdio 协议连接本地子进程 MCP Server"""
        if server_name in self.servers:
            logger.warning(f"MCP server {server_name} 已连接")
            return

        logger.info(f"[stdio] 连接 MCP server: {server_name} ({command} {' '.join(args)})")

        exit_stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=command, args=args, env=env or {})
            read_stream, write_stream = await exit_stack.enter_async_context(
                stdio_client(params)
            )
            session: ClientSession = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            self.servers[server_name] = {
                "session": session,
                "exit_stack": exit_stack,
                "transport": "stdio",
            }
            logger.info(f"[stdio] MCP server {server_name} 已连接")
        except Exception as e:
            await exit_stack.aclose()
            logger.error(f"[stdio] 连接 MCP server {server_name} 失败: {e}")
            raise

    async def connect_http_server(
        self,
        server_name: str,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """通过 streamable HTTP 连接远程 MCP Server

        Args:
            server_name: server 标识（用于工具路由）
            url: HTTP 端点（如 http://192.168.1.199:6620/mcp）
            headers: 可选 HTTP headers（鉴权时使用，本平台无需鉴权可传 None）
            timeout: 单次请求超时（秒）
        """
        if server_name in self.servers:
            logger.warning(f"MCP server {server_name} 已连接")
            return

        logger.info(f"[http] 连接 MCP server: {server_name} -> {url}")

        exit_stack = AsyncExitStack()
        try:
            # streamablehttp_client 返回 (read, write, get_session_id_callback)
            read_stream, write_stream, _get_session_id = await exit_stack.enter_async_context(
                streamablehttp_client(url=url, headers=headers, timeout=timeout)
            )
            session: ClientSession = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            init_result = await session.initialize()

            self.servers[server_name] = {
                "session": session,
                "exit_stack": exit_stack,
                "transport": "http",
                "url": url,
            }
            server_info = getattr(init_result, "serverInfo", None)
            if server_info is not None:
                logger.info(
                    f"[http] MCP server {server_name} 已连接 "
                    f"(serverInfo={server_info.name} v{server_info.version})"
                )
            else:
                logger.info(f"[http] MCP server {server_name} 已连接")
        except Exception as e:
            await exit_stack.aclose()
            logger.error(f"[http] 连接 MCP server {server_name} 失败: {e}")
            raise

    async def disconnect_server(self, server_name: str) -> None:
        """断开 MCP Server 连接"""
        if server_name not in self.servers:
            return
        try:
            await self.servers[server_name]["exit_stack"].aclose()
            del self.servers[server_name]
            logger.info(f"MCP server {server_name} 已断开")
        except Exception as e:
            logger.error(f"断开 MCP server {server_name} 失败: {e}")

    def list_servers(self) -> list[str]:
        return list(self.servers.keys())

    async def list_tools(self, server_name: str) -> list[dict]:
        """列出 MCP Server 暴露的工具"""
        if server_name not in self.servers:
            raise ValueError(f"MCP server {server_name} 未连接")

        session: ClientSession = self.servers[server_name]["session"]
        try:
            result = await session.list_tools()
            tools = []
            for tool in result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema or {},
                })
            return tools
        except Exception as e:
            logger.error(f"list_tools 失败: {e}")
            raise

    async def call_tool(
        self, server_name: str, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """调用 MCP 工具，返回结构化结果（dict）"""
        if server_name not in self.servers:
            return {"error": f"MCP server {server_name} 未连接"}

        session: ClientSession = self.servers[server_name]["session"]
        try:
            logger.info(f"调用 MCP 工具: {server_name}.{tool_name} args={args}")
            result = await session.call_tool(tool_name, args)

            # MCP 失败语义：isError=True 时，content 为可读错误文本
            if getattr(result, "isError", False):
                err_text = ""
                if result.content:
                    first = result.content[0]
                    err_text = getattr(first, "text", str(first))
                logger.warning(f"工具 {tool_name} 返回错误: {err_text}")
                return {"error": err_text or "tool returned isError=true"}

            # 优先使用 structuredContent（MCP 2025-06-18 规范的结构化输出）
            structured = getattr(result, "structuredContent", None)
            if structured:
                return structured

            # 退化到解析 TextContent
            if result.content:
                content = result.content[0]
                text = getattr(content, "text", None)
                if text is not None:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"result": text}

            return {"result": None}
        except Exception as e:
            logger.exception(f"call_tool 失败: {tool_name}")
            return {"error": f"{type(e).__name__}: {e}"}

    async def close(self) -> None:
        """关闭所有 server 连接"""
        for server_name in list(self.servers.keys()):
            await self.disconnect_server(server_name)


# 线程本地存储，每个线程/事件循环独立维护一个 MCP Client
_thread_local = threading.local()


async def get_mcp_client(force_reconnect: bool = False) -> MCPClient:
    """获取 MCP Client（线程本地单例，按 config.mcp.transport 选择 stdio / http）

    Args:
        force_reconnect: 强制重连（用于连接失效后的恢复）

    注意：每个线程/事件循环独立维护一个 MCP Client
        在不同线程调用时会自动创建新的连接，避免跨事件循环问题
    """
    # 从线程本地存储获取
    if not hasattr(_thread_local, 'client'):
        _thread_local.client = None

    client = _thread_local.client

    # 检查是否需要重连
    need_reconnect = force_reconnect
    if client is not None and not force_reconnect:
        # 检查 ksipms 服务是否已连接
        if "ksipms" not in client.servers:
            logger.warning("MCP Client 存在但 ksipms 连接丢失，尝试重连...")
            need_reconnect = True

    # 如果不需要重连且已存在，直接返回
    if client is not None and not need_reconnect:
        return client

    # 需要重连时先关闭旧连接
    if client is not None:
        try:
            await client.close()
        except Exception as e:
            logger.warning(f"关闭旧连接失败: {e}")
        _thread_local.client = None

    # 创建新连接
    client = MCPClient()
    if not client.enabled:
        _thread_local.client = client
        return client

    transport = (client.config.get("transport") or "stdio").lower()

    if transport == "http":
        endpoint = client.config.get("endpoint", "http://127.0.0.1:6620/mcp")
        timeout = float(client.config.get("timeout", 30))
        # 同事确认：MCP 调用层无需鉴权，鉴权在 MCP Server 内部完成
        await client.connect_http_server(
            server_name="ksipms",
            url=endpoint,
            headers=None,
            timeout=timeout,
        )
    else:
        # stdio 兼容路径（保留向后兼容，主流程已切到 http）
        await client.connect_stdio_server(
            server_name="ksipms",
            command="python",
            args=["-m", "mcp_servers.ksipms_server"],
            env={},
        )

    # 保存到线程本地存储
    _thread_local.client = client
    return client


async def reset_mcp_client() -> None:
    """重置线程本地 MCP Client（仅测试用）"""
    if hasattr(_thread_local, 'client') and _thread_local.client is not None:
        await _thread_local.client.close()
        _thread_local.client = None
