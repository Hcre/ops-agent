"""
tools/mcp_server.py — OpsAgent MCP Server

将现有工具通过 MCP 协议标准化暴露，供外部 MCP 客户端调用。
运行方式：python -m tools.mcp_server（stdio 模式）

工具列表与 ToolRegistry 保持同步，不重复定义 schema。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("OpsAgent")


# ---------------------------------------------------------------------------
# exec_bash
# ---------------------------------------------------------------------------

@mcp.tool()
async def exec_bash(
    cmd: str,
    timeout: float = 30.0,
    cmd_type: str = "read",
) -> str:
    """执行单条 bash 命令（支持管道/重定向）。

    Args:
        cmd: bash 命令字符串
        timeout: 超时秒数（默认 30s）
        cmd_type: 命令类型 read/file/service，决定使用哪个受限账号
    """
    from tools.exec_tools import exec_bash as _exec_bash
    result = await _exec_bash(cmd=cmd, timeout=timeout, cmd_type=cmd_type)
    if result.success:
        return result.output or "(无输出)"
    return f"[错误] {result.error}"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_file(path: str, max_bytes: int = 1_000_000) -> str:
    """读取文件内容。

    Args:
        path: 文件路径
        max_bytes: 最大读取字节数（防止 OOM，默认 1MB）
    """
    from tools.read_tools import read_file as _read_file
    result = await _read_file(path=path, max_bytes=max_bytes)
    if isinstance(result, str):
        return result
    return result.output if result.success else f"[错误] {result.error}"


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_dir(path: str) -> str:
    """列出目录内容。

    Args:
        path: 目录路径
    """
    from tools.read_tools import list_dir as _list_dir
    result = await _list_dir(path=path)
    if isinstance(result, str):
        return result
    return result.output if result.success else f"[错误] {result.error}"


# ---------------------------------------------------------------------------
# 感知工具
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_disk_detail(
    path: str = "/",
    mode: str = "summary",
) -> str:
    """查询磁盘使用情况（空间、inode、Top 目录、IO 统计）。

    Args:
        path: 任意文件或目录路径，工具自动定位挂载点
        mode: summary（默认）/ detail / full
    """
    from tools.perception_tools import get_disk_detail as _fn
    result = await _fn(path=path, mode=mode)
    return result.output if result.success else f"[错误] {result.error}"


@mcp.tool()
async def get_process_detail(
    pid: Optional[int] = None,
    name: Optional[str] = None,
    mode: str = "summary",
) -> str:
    """查询进程详细信息。name 匹配多个时返回列表；pid 不存在时自动检查 OOM 记录。

    Args:
        pid: 进程 PID
        name: 进程名或命令行关键字（模糊匹配）
        mode: summary / detail / full
    """
    from tools.perception_tools import get_process_detail as _fn
    kwargs = {"mode": mode}
    if pid is not None:
        kwargs["pid"] = pid
    if name is not None:
        kwargs["name"] = name
    result = await _fn(**kwargs)
    return result.output if result.success else f"[错误] {result.error}"


@mcp.tool()
async def get_logs(
    level: str = "err",
    n: int = 50,
    keyword: Optional[str] = None,
    since: str = "10 minutes ago",
    unit: Optional[str] = None,
    mode: str = "summary",
) -> str:
    """查询系统日志（journalctl）。支持按级别、服务、关键字、时间范围过滤。

    Args:
        level: 日志级别 debug/info/warning/err/crit
        n: 返回行数
        keyword: 关键字过滤
        since: 起始时间，如 '10 minutes ago'
        unit: systemd 服务名，如 nginx
        mode: summary / detail / full
    """
    from tools.perception_tools import get_logs as _fn
    kwargs = {"level": level, "n": n, "since": since, "mode": mode}
    if keyword is not None:
        kwargs["keyword"] = keyword
    if unit is not None:
        kwargs["unit"] = unit
    result = await _fn(**kwargs)
    return result.output if result.success else f"[错误] {result.error}"


@mcp.tool()
async def get_network_detail(
    interface: Optional[str] = None,
    mode: str = "summary",
) -> str:
    """查询网络详情：实时速率、TCP 状态统计、监听端口。

    Args:
        interface: 接口名，如 eth0；不填返回所有接口汇总
        mode: summary / detail / full
    """
    from tools.perception_tools import get_network_detail as _fn
    kwargs = {"mode": mode}
    if interface is not None:
        kwargs["interface"] = interface
    result = await _fn(**kwargs)
    return result.output if result.success else f"[错误] {result.error}"


@mcp.tool()
async def get_system_snapshot(mode: str = "summary") -> str:
    """获取全量系统快照（uptime、load、内存、磁盘告警、Top 进程）。

    Args:
        mode: summary / detail / full
    """
    from tools.perception_tools import get_system_snapshot as _fn
    result = await _fn(mode=mode)
    return result.output if result.success else f"[错误] {result.error}"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
