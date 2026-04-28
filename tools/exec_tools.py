"""
tools/exec_tools.py — 执行 bash 命令的工具

优先通过 PrivilegeBroker 以受限账号执行（最小权限原则）。
若 PrivilegeBroker 未初始化（sudo 环境未就绪），回退到直接执行并记录警告。
"""
import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from core.agent_loop import ToolResult

if TYPE_CHECKING:
    from security.privilege_broker import PrivilegeBroker

logger = logging.getLogger(__name__)

# 由 main.py 在启动时注入，PrivilegeBroker 初始化失败时保持 None
_broker: "PrivilegeBroker | None" = None


def set_privilege_broker(broker: "PrivilegeBroker") -> None:
    global _broker
    _broker = broker


async def exec_bash(cmd: str, timeout: float | None = None, cmd_type: str = "read") -> ToolResult:
    """执行单条 bash 命令。

    Args:
        cmd: bash 命令字符串（支持管道/重定向）
        timeout: 超时秒数，None 表示无限制（默认无超时，由用户自行中断）
        cmd_type: 命令类型 read/file/service，决定使用哪个受限账号

    Returns:
        ToolResult: 包含 success / output / error / exit_code
    """
    tool_call_id = str(uuid.uuid4())[:8]

    if _broker is not None:
        return await _exec_via_broker(cmd, cmd_type, tool_call_id, int(timeout) if timeout is not None else None)
    else:
        logger.warning("PrivilegeBroker 未初始化，回退到直接执行（仅限开发环境）")
        return await _exec_direct(cmd, timeout, tool_call_id)


async def _exec_via_broker(
    cmd: str,
    cmd_type: str,
    tool_call_id: str,
    timeout: int | None,
) -> ToolResult:
    """通过 PrivilegeBroker 以受限账号执行命令。

    cmd_type 由 PermissionManager 判断后传入，在此转换为 Privilege，
    不接受来自 LLM 的原始字符串直接控制执行账号。
    """
    from security.privilege_broker import PrivilegeBroker
    privilege = PrivilegeBroker.category_to_privilege(cmd_type)

    loop = asyncio.get_event_loop()
    exec_result = await loop.run_in_executor(
        None,
        lambda: _broker.execute(cmd, tool_call_id, privilege, timeout),  # type: ignore[union-attr]
    )

    if exec_result.success:
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="exec_bash",
            success=True,
            output=exec_result.stdout,
            elapsed_ms=exec_result.elapsed_ms,
            exit_code=exec_result.exit_code,
        )
    else:
        error_msg = exec_result.stderr or f"命令失败，退出码: {exec_result.exit_code}"
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="exec_bash",
            success=False,
            output=exec_result.stdout,
            error=error_msg,
            elapsed_ms=exec_result.elapsed_ms,
            exit_code=exec_result.exit_code,
        )


async def _exec_direct(cmd: str, timeout: float | None, tool_call_id: str) -> ToolResult:
    """直接执行（无权限隔离，仅用于开发/测试环境）。"""
    t0 = time.monotonic()
    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            coro = process.communicate()
            stdout, stderr = await (
                asyncio.wait_for(coro, timeout=timeout) if timeout is not None else coro
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
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="exec_bash",
                success=False,
                output=output_text,
                error=error_text or f"命令失败，退出码: {exit_code}",
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
