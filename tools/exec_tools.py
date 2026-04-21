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


async def exec_bash(cmd: str, timeout: float = 30.0, cmd_type: str = "read") -> ToolResult:
    """执行单条 bash 命令。

    Args:
        cmd: bash 命令字符串（支持管道/重定向）
        timeout: 超时秒数（默认 30s）
        cmd_type: 命令类型 read/file/service，决定使用哪个受限账号

    Returns:
        ToolResult: 包含 success / output / error / exit_code
    """
    tool_call_id = str(uuid.uuid4())[:8]

    if _broker is not None:
        return await _exec_via_broker(cmd, cmd_type, tool_call_id, int(timeout))
    else:
        logger.warning("PrivilegeBroker 未初始化，回退到直接执行（仅限开发环境）")
        return await _exec_direct(cmd, timeout, tool_call_id)


async def _exec_via_broker(
    cmd: str,
    cmd_type: str,
    tool_call_id: str,
    timeout: int,
) -> ToolResult:
    """通过 PrivilegeBroker 以受限账号执行命令。"""
    if _broker is None:
        raise RuntimeError("PrivilegeBroker 未初始化")  # 主动失败而非静默回退
    loop = asyncio.get_event_loop()

    # PrivilegeBroker.execute 是同步阻塞调用，放到线程池避免阻塞事件循环
    exec_result = await loop.run_in_executor(
        None,
        lambda: _broker.execute(cmd, cmd_type, tool_call_id, timeout),  # type: ignore[union-attr]
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


async def _exec_direct(cmd: str, timeout: float, tool_call_id: str) -> ToolResult:
    """直接执行（无权限隔离，仅用于开发/测试环境）。"""
    t0 = time.monotonic()
    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
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
