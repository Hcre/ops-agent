"""
tools/registry.py — 工具注册表与 OpenAI function calling schema 生成
"""
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from .exec_tools import exec_bash
from .read_tools import read_file, list_dir
from .perception_tools import (
    get_disk_detail,
    get_process_detail,
    get_logs,
    get_network_detail,
    get_system_snapshot,
)

ToolCategory = Literal["read", "file", "service", "unknown"]
ToolSource = Literal["builtin", "mcp"]


@dataclass(frozen=True)
class ToolEntry:
    name: str
    handler: Callable[..., Awaitable[Any]]
    schema: dict[str, Any]
    category: ToolCategory
    source: ToolSource


class ToolRegistry:
    """工具注册表，管理所有可用工具及其 schema。"""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolEntry] = {}
        self._register_builtin_tools()
        self._register_perception_tools()

    def register(
        self,
        tool_name: str,
        handler: Callable,
        param_spec: dict[str, Any],
        category: ToolCategory = "read",
        source: ToolSource = "builtin",
    ) -> None:
        """注册工具。自动将同步函数包装为异步。"""
        if not inspect.iscoroutinefunction(handler):
            _orig = handler
            async def async_wrapper(**kwargs: Any) -> Any:
                return _orig(**kwargs)
            async_handler: Callable = async_wrapper
        else:
            async_handler = handler

        self._tools[tool_name] = ToolEntry(
            name=tool_name,
            handler=async_handler,
            schema=self._build_schema(tool_name, param_spec),
            category=category,
            source=source,
        )

    # ── 兼容性适配层（agent_loop.py 无需改动）────────────────────────────────

    @property
    def handlers(self) -> dict[str, Callable]:
        return {name: entry.handler for name, entry in self._tools.items()}

    def get_handler(self, tool_name: str) -> Callable | None:
        entry = self._tools.get(tool_name)
        return entry.handler if entry else None

    def register_with_schema(
        self,
        tool_name: str,
        handler: Callable,
        openai_schema: dict[str, Any],
        category: ToolCategory = "unknown",
        source: ToolSource = "mcp",
    ) -> None:
        """直接用完整 OpenAI function calling schema 注册工具，跳过 _build_schema 转换。
        供 MCP Client 使用，避免 JSON Schema → param_spec → JSON Schema 的无意义往返。
        """
        if not inspect.iscoroutinefunction(handler):
            _orig = handler
            async def async_wrapper(**kwargs: Any) -> Any:
                return _orig(**kwargs)
            async_handler: Callable = async_wrapper
        else:
            async_handler = handler

        self._tools[tool_name] = ToolEntry(
            name=tool_name,
            handler=async_handler,
            schema=openai_schema,
            category=category,
            source=source,
        )

    # ── 新增接口 ──────────────────────────────────────────────────────────────

    def get_entry(self, tool_name: str) -> Optional[ToolEntry]:
        """获取完整工具条目（含 category/source），供权限引擎和 MCP 路由使用。"""
        return self._tools.get(tool_name)

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        """统一异步调用接口。"""
        entry = self._tools.get(tool_name)
        if not entry:
            raise ValueError(f"Tool not found: {tool_name}")
        return await entry.handler(**kwargs)

    def get_schemas(self) -> list[dict[str, Any]]:
        return [entry.schema for entry in self._tools.values()]

    def get_schema(self, tool_name: str) -> dict[str, Any] | None:
        entry = self._tools.get(tool_name)
        return entry.schema if entry else None

    # ── 内置工具注册 ──────────────────────────────────────────────────────────

    def _register_builtin_tools(self) -> None:
        self.register("exec_bash", exec_bash, {
            "description": "执行单条 bash 命令（支持管道/重定向）",
            "parameters": {
                "cmd":      {"type": "string", "description": "bash 命令字符串"},
                "timeout":  {"type": "number", "description": "超时秒数", "default": 30.0},
                "cmd_type": {"type": "string", "description": "命令类型 read/file/service", "default": "read"},
            },
            "required": ["cmd"],
        }, category="unknown")  # exec_bash 的 category 由运行时 CommandRiskResult 决定

        self.register("read_file", read_file, {
            "description": "读取文件内容",
            "parameters": {
                "path":      {"type": "string",  "description": "文件路径"},
                "max_bytes": {"type": "integer", "description": "最大读取字节数", "default": 1_000_000},
            },
            "required": ["path"],
        }, category="read")

        self.register("list_dir", list_dir, {
            "description": "列出目录内容",
            "parameters": {
                "path": {"type": "string", "description": "目录路径"},
            },
            "required": ["path"],
        }, category="read")

    def _register_perception_tools(self) -> None:
        MODE = {"type": "string", "description": "查询模式：summary/detail/full", "default": "summary"}

        self.register("get_disk_detail", get_disk_detail, {
            "description": "查询磁盘使用情况（空间、inode、Top 目录、IO 统计）",
            "parameters": {
                "path": {"type": "string", "description": "文件或目录路径，自动定位挂载点", "default": "/"},
                "mode": MODE,
            },
            "required": [],
        }, category="read")

        self.register("get_process_detail", get_process_detail, {
            "description": "查询进程详细信息。name 匹配多个时返回列表；pid 不存在时检查 OOM 记录",
            "parameters": {
                "pid":  {"type": "integer", "description": "进程 PID"},
                "name": {"type": "string",  "description": "进程名或命令行关键字（模糊匹配）"},
                "mode": MODE,
            },
            "required": [],
        }, category="read")

        self.register("get_logs", get_logs, {
            "description": "查询系统日志（journalctl）。支持按级别、服务、关键字、时间范围过滤",
            "parameters": {
                "level":   {"type": "string",  "description": "日志级别：debug/info/warning/err/crit", "default": "err"},
                "n":       {"type": "integer", "description": "返回行数", "default": 50},
                "keyword": {"type": "string",  "description": "关键字过滤"},
                "since":   {"type": "string",  "description": "起始时间，如 '10 minutes ago'", "default": "10 minutes ago"},
                "unit":    {"type": "string",  "description": "systemd 服务名，如 nginx"},
                "mode":    MODE,
            },
            "required": [],
        }, category="read")

        self.register("get_network_detail", get_network_detail, {
            "description": "查询网络详情：实时速率、TCP 状态统计、监听端口",
            "parameters": {
                "interface": {"type": "string", "description": "接口名，如 eth0；不填返回所有接口"},
                "mode":      MODE,
            },
            "required": [],
        }, category="read")

        self.register("get_system_snapshot", get_system_snapshot, {
            "description": "获取全量系统快照（uptime、load、内存、磁盘告警、Top 进程）",
            "parameters": {
                "mode": MODE,
            },
            "required": [],
        }, category="read")

    # ── Schema 构建 ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_schema(tool_name: str, param_spec: dict[str, Any]) -> dict[str, Any]:
        properties = {
            k: {"type": v.get("type", "string"), "description": v.get("description", "")}
            for k, v in param_spec.get("parameters", {}).items()
        }
        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": param_spec.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": param_spec.get("required", []),
                },
            },
        }
