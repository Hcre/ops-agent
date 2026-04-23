"""
tools/mcp_client.py — MCP Client 管理器

启动时读取 mcp_servers.json，连接每个外部 MCP Server，
将其工具动态注册进 ToolRegistry，工具名格式：mcp__<server>__<tool>。

所有 MCP 工具调用仍经过 PermissionManager，不绕过安全管道。
Server 连接失败时打印警告继续启动，不阻断主流程。
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent.parent / "mcp_servers.json"


class MCPClientManager:
    """管理所有外部 MCP Server 的连接与工具注册。"""

    def __init__(self, registry: "ToolRegistry", config_path: str | None = None) -> None:
        self._registry = registry
        self._config_path = Path(config_path or os.getenv("MCP_SERVERS_CONFIG", str(_DEFAULT_CONFIG)))
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stack = AsyncExitStack()

    async def start(self) -> None:
        """读取配置，连接所有 Server，注册工具。"""
        if not self._config_path.exists():
            logger.info("未找到 mcp_servers.json，跳过 MCP Client 初始化")
            return

        try:
            config = json.loads(self._config_path.read_text())
        except Exception as e:
            logger.warning("mcp_servers.json 解析失败: %s", e)
            return

        for server_cfg in config.get("servers", []):
            name = server_cfg.get("name", "")
            if not name:
                continue
            try:
                await self._connect_server(name, server_cfg)
                logger.info("MCP Server 连接成功: %s", name)
            except Exception as e:
                logger.warning("MCP Server 连接失败，跳过: %s — %s", name, e)

    async def _connect_server(self, name: str, cfg: dict[str, Any]) -> None:
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env"),
        )
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(params))
        session = await self._exit_stack.enter_async_context(
            ClientSession(*stdio_transport)
        )
        await session.initialize()
        self._sessions[name] = session
        await self._register_server_tools(name, session)

    async def _register_server_tools(self, server_name: str, session: ClientSession) -> None:
        """拉取 Server 工具列表，动态注册进 ToolRegistry。"""
        result = await session.list_tools()
        for tool in result.tools:
            mcp_tool_name = f"mcp__{server_name}__{tool.name}"
            _session = session
            _tool_name = tool.name

            async def mcp_handler(_s=_session, _t=_tool_name, **kwargs: Any) -> str:
                call_result = await _s.call_tool(_t, arguments=kwargs)
                if call_result.content:
                    first = call_result.content[0]
                    return getattr(first, "text", str(first))
                return ""

            # MCP tool.inputSchema 本身就是 JSON Schema，直接构造 OpenAI schema，无需中间转换
            input_schema = tool.inputSchema or {}
            openai_schema = {
                "type": "function",
                "function": {
                    "name": mcp_tool_name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": input_schema.get("properties", {}),
                        "required": input_schema.get("required", []),
                    },
                },
            }

            self._registry.register_with_schema(
                tool_name=mcp_tool_name,
                handler=mcp_handler,
                openai_schema=openai_schema,
                category="unknown",
                source="mcp",
            )
            logger.debug("注册 MCP 工具: %s", mcp_tool_name)

    async def stop(self) -> None:
        """关闭所有 Server 连接。"""
        await self._exit_stack.aclose()
        self._sessions.clear()
