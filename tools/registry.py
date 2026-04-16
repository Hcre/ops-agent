"""
tools/registry.py — 工具注册表与 OpenAI function calling schema 生成

对应 Week 2 M3：注册 exec_bash / read_file / list_dir，生成 OpenAI function calling schema
"""
import json
from typing import Any, Callable, Literal

from .exec_tools import exec_bash
from .read_tools import read_file, list_dir
from .perception_tools import (
    get_disk_detail,
    get_process_detail,
    get_logs,
    get_network_detail,
    get_system_snapshot,
)


class ToolRegistry:
    """工具注册表，管理所有可用工具及其 schema"""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._register_builtin_tools()
        self._register_perception_tools()

    def _register_builtin_tools(self) -> None:
        """注册内置工具"""
        self.register("exec_bash", exec_bash, {
            "description": "执行单条 bash 命令",
            "parameters": {
                "cmd": {
                    "type": "string",
                    "description": "bash 命令字符串"
                },
                "timeout": {
                    "type": "number",
                    "description": "超时秒数（默认 30s）",
                    "default": 30.0
                }
            },
            "required": ["cmd"]
        })

        self.register("read_file", read_file, {
            "description": "读取文件内容",
            "parameters": {
                "path": {
                    "type": "string",
                    "description": "文件路径"
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "最大读取字节数（防止 OOM）",
                    "default": 1_000_000
                }
            },
            "required": ["path"]
        })

        self.register("list_dir", list_dir, {
            "description": "列出目录内容",
            "parameters": {
                "path": {
                    "type": "string",
                    "description": "目录路径"
                }
            },
            "required": ["path"]
        })

    def _register_perception_tools(self) -> None:
        """注册感知按需查询工具"""
        MODE_PARAM = {
            "type": "string",
            "description": "查询模式：summary（默认，摘要）/ detail（详细）/ full（完整原始数据）",
            "default": "summary",
        }

        self.register("get_disk_detail", get_disk_detail, {
            "description": "查询指定路径所在挂载点的磁盘使用情况（空间、inode、Top 目录、IO 统计）",
            "parameters": {
                "path": {"type": "string", "description": "任意文件或目录路径，工具自动定位挂载点", "default": "/"},
                "mode": MODE_PARAM,
            },
            "required": [],
        })

        self.register("get_process_detail", get_process_detail, {
            "description": "查询进程详细信息。name 匹配多个时返回列表供选择；pid 不存在时自动检查 OOM 记录",
            "parameters": {
                "pid":  {"type": "integer", "description": "进程 PID"},
                "name": {"type": "string",  "description": "进程名或命令行关键字（模糊匹配）"},
                "mode": MODE_PARAM,
            },
            "required": [],
        })

        self.register("get_logs", get_logs, {
            "description": "查询系统日志（journalctl）。支持按级别、服务、关键字、时间范围过滤",
            "parameters": {
                "level":   {"type": "string",  "description": "日志级别：debug/info/warning/err/crit", "default": "err"},
                "n":       {"type": "integer", "description": "返回行数", "default": 50},
                "keyword": {"type": "string",  "description": "关键字过滤（grep -i）"},
                "since":   {"type": "string",  "description": "起始时间，如 '10 minutes ago'", "default": "10 minutes ago"},
                "unit":    {"type": "string",  "description": "systemd 服务名，如 nginx、mysql"},
                "mode":    MODE_PARAM,
            },
            "required": [],
        })

        self.register("get_network_detail", get_network_detail, {
            "description": "查询网络详情：实时速率（含 errors/drops）、TCP 状态统计、监听端口。interface=None 返回所有接口",
            "parameters": {
                "interface": {"type": "string", "description": "接口名，如 eth0；不填返回所有接口汇总"},
                "mode":      MODE_PARAM,
            },
            "required": [],
        })

        self.register("get_system_snapshot", get_system_snapshot, {
            "description": "获取全量系统快照（uptime、load、内存、磁盘告警、Top 进程）。token 消耗较大，建议先用感知摘要",
            "parameters": {
                "mode": MODE_PARAM,
            },
            "required": [],
        })

    def register(
        self,
        tool_name: str,
        handler: Callable,
        param_spec: dict[str, Any]
    ) -> None:
        """
        注册工具

        Args:
            tool_name: 工具名称
            handler: 可调用的工具函数
            param_spec: 参数规范 {param_name: {type, description, default?, ...}, ...}
        """
        self._handlers[tool_name] = handler
        self._schemas[tool_name] = self._build_schema(tool_name, param_spec)

    @property
    def handlers(self) -> dict[str, Callable]:
        """返回所有工具处理函数"""
        return self._handlers

    def get_handler(self, tool_name: str) -> Callable | None:
        """获取工具处理函数"""
        return self._handlers.get(tool_name)

    def get_schemas(self) -> list[dict[str, Any]]:
        """获取所有工具的 OpenAI function calling schema"""
        return list(self._schemas.values())

    def get_schema(self, tool_name: str) -> dict[str, Any] | None:
        """获取单个工具的 schema"""
        return self._schemas.get(tool_name)

    @staticmethod
    def _build_schema(
        tool_name: str,
        param_spec: dict[str, Any]
    ) -> dict[str, Any]:
        """
        构建 OpenAI function calling schema

        Args:
            tool_name: 工具名称
            param_spec: 参数规范

        Returns:
            OpenAI function calling schema
        """
        description = param_spec.get("description", "")
        parameters = param_spec.get("parameters", {})
        required = param_spec.get("required", [])

        # 构建 properties
        properties = {}
        for param_name, param_info in parameters.items():
            properties[param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", "")
            }
            if "default" in param_info:
                properties[param_name]["default"] = param_info["default"]

        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
