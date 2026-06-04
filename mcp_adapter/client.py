"""
MCP 客户端 - 支持 stdio 协议连接 MCP Server

阶段 2：实现标准 MCP 协议，对接本地 MCP Server
"""
import asyncio
import json
from typing import Dict, Any
from loguru import logger

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from utils import CONFIG


class MCPClient:
    """MCP 客户端（stdio 协议）"""

    def __init__(self):
        self.config = CONFIG.get("mcp", {})
        self.enabled = self.config.get("enabled", False)
        self.servers: dict[str, dict] = {}  # server_name -> {session, read, write}

        if not self.enabled:
            logger.warning("MCP 未启用，所有工具调用走 mock")

    async def connect_server(self, server_name: str, command: str, args: list[str],
                            env: dict[str, str] | None = None) -> None:
        """连接到 MCP Server

        Args:
            server_name: Server 名称（如 "ksipms"）
            command: 启动命令（如 "python"）
            args: 命令参数（如 ["-m", "mcp_servers.ksipms_server"]）
            env: 环境变量
        """
        if server_name in self.servers:
            logger.warning(f"MCP server {server_name} 已连接")
            return

        logger.info(f"连接 MCP server: {server_name}")

        try:
            params = StdioServerParameters(
                command=command,
                args=args,
                env=env or {}
            )

            # stdio_client 返回异步上下文管理器
            stdio = stdio_client(params)
            read_stream, write_stream = await stdio.__aenter__()

            session = ClientSession(read_stream, write_stream)
            await session.initialize()

            self.servers[server_name] = {
                "session": session,
                "read": read_stream,
                "write": write_stream,
                "stdio": stdio,  # 保存上下文管理器用于清理
            }

            logger.info(f"MCP server {server_name} 已连接")

        except Exception as e:
            logger.error(f"连接 MCP server {server_name} 失败: {e}")
            raise

    async def disconnect_server(self, server_name: str) -> None:
        """断开 MCP Server 连接"""
        if server_name not in self.servers:
            return

        try:
            server_info = self.servers[server_name]
            # 退出上下文管理器
            if "stdio" in server_info:
                await server_info["stdio"].__aexit__(None, None, None)
            del self.servers[server_name]
            logger.info(f"MCP server {server_name} 已断开")
        except Exception as e:
            logger.error(f"断开 MCP server {server_name} 失败: {e}")

    def list_servers(self) -> list[str]:
        """列出已连接的 server"""
        return list(self.servers.keys())

    async def list_tools(self, server_name: str) -> list[dict]:
        """列出 MCP Server 暴露的工具

        Returns:
            [{"name": "query_alarms", "description": "...", "inputSchema": {...}}, ...]
        """
        if server_name not in self.servers:
            raise ValueError(f"MCP server {server_name} 未连接")

        session = self.servers[server_name]["session"]

        try:
            result = await session.list_tools()
            tools = []
            for tool in result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema or {}
                })
            return tools
        except Exception as e:
            logger.error(f"list_tools 失败: {e}")
            raise

    async def call_tool(self, server_name: str, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """调用 MCP 工具

        Args:
            server_name: Server 名称
            tool_name: 工具名称
            args: 参数字典

        Returns:
            结果字典（JSON 格式）
        """
        if server_name not in self.servers:
            raise ValueError(f"MCP server {server_name} 未连接")

        session = self.servers[server_name]["session"]

        try:
            logger.info(f"调用 MCP 工具: {server_name}.{tool_name}")
            result = await session.call_tool(tool_name, args)

            # 解析返回的 TextContent
            if result.content and len(result.content) > 0:
                content = result.content[0]
                if hasattr(content, 'text'):
                    # 尝试解析为 JSON
                    try:
                        return json.loads(content.text)
                    except json.JSONDecodeError:
                        return {"result": content.text}

            return {"result": str(result.content)}

        except Exception as e:
            logger.exception(f"call_tool 失败: {tool_name}")
            return {"error": f"{type(e).__name__}: {e}"}

    async def close(self) -> None:
        """关闭所有连接"""
        for server_name in list(self.servers.keys()):
            await self.disconnect_server(server_name)


# 全局单例
_mcp_client = None


async def get_mcp_client() -> MCPClient:
    """获取 MCP Client 单例"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()

        # 如果配置中启用了 MCP，自动连接默认 server
        if _mcp_client.enabled:
            # 连接 ksipms server
            await _mcp_client.connect_server(
                server_name="ksipms",
                command="python",
                args=["-m", "mcp_servers.ksipms_server"],
                env={}
            )

    return _mcp_client
