"""
core/error_recovery.py — 错误恢复策略

对应 Week 2 M7：三种恢复策略
1. Backoff: 指数退避重试
2. Compact: 上下文压缩后重试
3. Fallback: 降级到只读模式
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from config import AgentConfig


class RecoveryStrategy(Enum):
    """恢复策略"""
    BACKOFF = "backoff"      # 指数退避
    COMPACT = "compact"      # 上下文压缩
    FALLBACK = "fallback"    # 降级到只读


@dataclass
class RecoveryAttempt:
    """恢复尝试记录"""
    strategy: RecoveryStrategy
    attempt_num: int
    delay_ms: int
    success: bool
    error: str | None = None


class ErrorRecovery:
    """错误恢复管理器。

    三种恢复策略：
    1. Backoff: 指数退避重试（网络抖动、临时错误）
    2. Compact: 上下文压缩后重试（上下文溢出）
    3. Fallback: 降级到只读模式（持续失败）
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._backoff_base = config.backoff_base_delay
        self._backoff_max = config.backoff_max_delay
        self._max_attempts = config.max_recovery_attempts
        self._attempts: list[RecoveryAttempt] = []

    async def backoff_retry(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """指数退避重试。

        Args:
            fn: 要重试的函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数返回值

        Raises:
            Exception: 所有重试都失败
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                if attempt >= self._max_attempts:
                    raise

                # 计算退避延迟
                delay = min(
                    self._backoff_base * (2 ** (attempt - 1)),
                    self._backoff_max,
                )
                delay_ms = int(delay * 1000)

                self._attempts.append(
                    RecoveryAttempt(
                        strategy=RecoveryStrategy.BACKOFF,
                        attempt_num=attempt,
                        delay_ms=delay_ms,
                        success=False,
                        error=str(e),
                    )
                )

                await asyncio.sleep(delay)

    async def compact_retry(
        self,
        fn: Callable[..., Any],
        compact_fn: Callable[[], None],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """上下文压缩后重试。

        Args:
            fn: 要重试的函数
            compact_fn: 压缩函数（无参数）
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数返回值

        Raises:
            Exception: 压缩后仍然失败
        """
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            # 尝试压缩
            try:
                compact_fn()
                self._attempts.append(
                    RecoveryAttempt(
                        strategy=RecoveryStrategy.COMPACT,
                        attempt_num=1,
                        delay_ms=0,
                        success=True,
                    )
                )
                # 压缩后重试
                return await fn(*args, **kwargs)
            except Exception as compact_error:
                self._attempts.append(
                    RecoveryAttempt(
                        strategy=RecoveryStrategy.COMPACT,
                        attempt_num=1,
                        delay_ms=0,
                        success=False,
                        error=str(compact_error),
                    )
                )
                raise

    def fallback_to_readonly(self) -> None:
        """降级到只读模式。

        记录降级事件，调用者应该将 permission_mode 改为 "plan"。
        """
        self._attempts.append(
            RecoveryAttempt(
                strategy=RecoveryStrategy.FALLBACK,
                attempt_num=1,
                delay_ms=0,
                success=True,
            )
        )

    def get_attempts(self) -> list[RecoveryAttempt]:
        """获取所有恢复尝试记录。"""
        return self._attempts.copy()

    def clear_attempts(self) -> None:
        """清空恢复尝试记录。"""
        self._attempts.clear()

    def should_fallback(self) -> bool:
        """判断是否应该降级到只读模式。

        如果连续失败次数达到阈值，返回 True。
        """
        if not self._attempts:
            return False

        # 统计最近的失败次数
        recent_failures = 0
        for attempt in reversed(self._attempts):
            if not attempt.success:
                recent_failures += 1
            else:
                break

        return recent_failures >= self._max_attempts
