"""
tools/read_tools.py — 读文件和列目录的工具

对应 Week 2 M2：读取文件内容、列出目录，返回 ToolResult 结构
"""
import json
import time
import uuid
from pathlib import Path

from core.agent_loop import ToolResult


async def read_file(path: str, max_bytes: int = 1_000_000) -> ToolResult:
    """
    读取文件内容

    Args:
        path: 文件路径
        max_bytes: 最大读取字节数（防止 OOM）

    Returns:
        ToolResult: 包含文件内容或错误
    """
    tool_call_id = str(uuid.uuid4())[:8]
    t0 = time.monotonic()

    try:
        file_path = Path(path).resolve()

        # 检查文件是否存在
        if not file_path.exists():
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="read_file",
                success=False,
                output="",
                error=f"文件不存在: {path}",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        # 检查是否是文件
        if not file_path.is_file():
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="read_file",
                success=False,
                output="",
                error=f"不是文件: {path}",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        # 检查文件大小
        file_size = file_path.stat().st_size
        if file_size > max_bytes:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="read_file",
                success=False,
                output="",
                error=f"文件过大: {file_size} 字节 > {max_bytes} 字节限制",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        # 读取文件
        content = file_path.read_text(encoding="utf-8", errors="replace")
        elapsed_ms = (time.monotonic() - t0) * 1000

        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="read_file",
            success=True,
            output=content,
            elapsed_ms=elapsed_ms,
            exit_code=0,
        )

    except PermissionError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="read_file",
            success=False,
            output="",
            error="权限不足",
            elapsed_ms=elapsed_ms,
            exit_code=-1,
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="read_file",
            success=False,
            output="",
            error=str(e),
            elapsed_ms=elapsed_ms,
            exit_code=-1,
        )


async def list_dir(path: str) -> ToolResult:
    """
    列出目录内容

    Args:
        path: 目录路径

    Returns:
        ToolResult: 包含目录列表（JSON 格式）或错误
    """
    tool_call_id = str(uuid.uuid4())[:8]
    t0 = time.monotonic()

    try:
        dir_path = Path(path).resolve()

        # 检查目录是否存在
        if not dir_path.exists():
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="list_dir",
                success=False,
                output="",
                error=f"目录不存在: {path}",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        # 检查是否是目录
        if not dir_path.is_dir():
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="list_dir",
                success=False,
                output="",
                error=f"不是目录: {path}",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        # 列出目录内容
        entries = []
        for item in sorted(dir_path.iterdir()):
            try:
                stat = item.stat()
                entry = {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": stat.st_size,
                }
                entries.append(entry)
            except (PermissionError, OSError):
                # 跳过无法访问的项
                pass

        output_json = json.dumps({"entries": entries}, ensure_ascii=False, indent=2)
        elapsed_ms = (time.monotonic() - t0) * 1000

        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_dir",
            success=True,
            output=output_json,
            elapsed_ms=elapsed_ms,
            exit_code=0,
        )

    except PermissionError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_dir",
            success=False,
            output="",
            error="权限不足",
            elapsed_ms=elapsed_ms,
            exit_code=-1,
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_dir",
            success=False,
            output="",
            error=str(e),
            elapsed_ms=elapsed_ms,
            exit_code=-1,
        )
