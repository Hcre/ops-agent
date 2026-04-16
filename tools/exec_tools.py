"""
tools/exec_tools.py — 执行 bash 命令的工具

对应 Week 2 M1：安全地执行 bash 命令，返回 ToolResult 结构
"""
import asyncio
import time
import uuid

from core.agent_loop import ToolResult


async def exec_bash(cmd: str, timeout: float = 30.0) -> ToolResult:
    """
    执行单条 bash 命令

    Args:
        cmd: bash 命令字符串
        timeout: 超时秒数（默认 30s）

    Returns:
        ToolResult: 包含 success / output / error / exit_code
    """
    tool_call_id = str(uuid.uuid4())[:8]
    t0 = time.monotonic()

    try:
        # 使用 asyncio 创建子进程
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="exec_bash",
                success=False,
                output="",
                error=f"命令超时（{timeout}s）",
                elapsed_ms=elapsed_ms,
                exit_code=-1,
            )

        exit_code = process.returncode or 0
        output_text = stdout.decode("utf-8", errors="replace").strip()
        error_text = stderr.decode("utf-8", errors="replace").strip()

        elapsed_ms = (time.monotonic() - t0) * 1000

        if exit_code == 0:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="exec_bash",
                success=True,
                output=output_text,
                elapsed_ms=elapsed_ms,
                exit_code=exit_code,
            )
        else:
            # 命令执行失败
            error_msg = error_text if error_text else f"命令失败，退出码: {exit_code}"
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="exec_bash",
                success=False,
                output=output_text,
                error=error_msg,
                elapsed_ms=elapsed_ms,
                exit_code=exit_code,
            )

    except PermissionError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="exec_bash",
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
            tool_name="exec_bash",
            success=False,
            output="",
            error=str(e),
            elapsed_ms=elapsed_ms,
            exit_code=-1,
        )
