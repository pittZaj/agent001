"""
MCP 客户端 - 支持 stdio 与 streamable HTTP 两种传输

阶段 2.5.1 改造点：
- 新增 HTTP 传输（streamablehttp_client），对接真实平台 192.168.1.199:6620/mcp
- 用 AsyncExitStack 统一管理 stdio/http 上下文 + ClientSession 的生命周期
  （旧实现没把 ClientSession 作为 async context manager 进入，后台接收循环未启动，
   会导致 initialize 卡住或工具调用永久挂起，这次一并修复）
- 鉴权透明化：MCP Server 内部已处理后端鉴权，Python 侧不需要传 AK/SK
"""
import json
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


# 全局单例
_mcp_client: Optional[MCPClient] = None


async def get_mcp_client(force_reconnect: bool = False) -> MCPClient:
    """获取 MCP Client 单例（按 config.mcp.transport 选择 stdio / http）

    Args:
        force_reconnect: 强制重连（用于连接失效后的恢复）
    """
    global _mcp_client

    # 如果已存在且不需要重连，检查连接是否有效
    if _mcp_client is not None and not force_reconnect:
        # 检查 ksipms 服务是否已连接
        if "ksipms" in _mcp_client.servers:
            return _mcp_client
        else:
            logger.warning("MCP Client 存在但 ksipms 连接丢失，尝试重连...")
            force_reconnect = True

    # 需要重连时先关闭旧连接
    if force_reconnect and _mcp_client is not None:
        try:
            await _mcp_client.close()
        except Exception as e:
            logger.warning(f"关闭旧连接失败: {e}")
        _mcp_client = None

    _mcp_client = MCPClient()
    if not _mcp_client.enabled:
        return _mcp_client

    transport = (_mcp_client.config.get("transport") or "stdio").lower()

    if transport == "http":
        endpoint = _mcp_client.config.get("endpoint", "http://127.0.0.1:6620/mcp")
        timeout = float(_mcp_client.config.get("timeout", 30))
        # 同事确认：MCP 调用层无需鉴权，鉴权在 MCP Server 内部完成
        await _mcp_client.connect_http_server(
            server_name="ksipms",
            url=endpoint,
            headers=None,
            timeout=timeout,
        )
    else:
        # stdio 兼容路径（保留向后兼容，主流程已切到 http）
        await _mcp_client.connect_stdio_server(
            server_name="ksipms",
            command="python",
            args=["-m", "mcp_servers.ksipms_server"],
            env={},
        )

    return _mcp_client


async def reset_mcp_client() -> None:
    """重置全局单例（仅测试用）"""
    global _mcp_client
    if _mcp_client is not None:
        await _mcp_client.close()
        _mcp_client = None
