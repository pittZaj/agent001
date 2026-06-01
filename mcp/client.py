"""
MCP 客户端（占位，阶段 2 实现）

阶段 1：所有工具调用走 graph/nodes.py 里的 _execute_task_mock
阶段 2：替换为真实 MCP 调用，对接 ksipms 平台
"""
from typing import Dict, Any
from loguru import logger
from utils import CONFIG


class MCPClient:
    """MCP 客户端（待实现）"""

    def __init__(self):
        self.endpoint = CONFIG["mcp"]["endpoint"]
        self.enabled = CONFIG["mcp"]["enabled"]
        if not self.enabled:
            logger.warning("MCP 未启用，所有工具调用走 mock")

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """调用 MCP 工具

        阶段 2 实现细节：
        - 用 httpx.AsyncClient 发起请求到 self.endpoint
        - 按 MCP 协议封装 jsonrpc 请求
        - 处理超时、重试、鉴权
        """
        raise NotImplementedError("MCP 客户端待阶段 2 实现")


# 全局单例
_mcp_client = None

def get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
