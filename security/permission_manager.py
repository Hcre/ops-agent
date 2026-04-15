"""
security/permission_manager.py — 权限管理器

对应 s07 Permission System
4 步决策管道：deny → mode → allow → ask
三种运行模式：default / plan / auto
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from config import ABSOLUTE_BLACKLIST, HIGH_RISK_PREFIXES, READ_PREFIXES

if TYPE_CHECKING:
    from config import AgentConfig

DecisionBehavior = Literal["allow", "ask", "deny"]


@dataclass
class PermissionDecision:
    behavior: DecisionBehavior
    reason: str
    risk_level: str = "LOW"


class PermissionManager:
    """工具调用权限决策器。

    Week 1 stub：接口完整，但 check() 对所有操作返回 allow（只记录日志）。
    Week 2 替换为真实的 4 步管道实现。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._mode: str = config.mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def check(self, tool_name: str, tool_args: dict) -> PermissionDecision:
        """4 步决策管道（Week 1 stub：全部 allow）。

        完整管道（Week 2 实现）：
        Step 1: deny_rules — 绝对黑名单
        Step 2: mode_check — plan 模式拒绝写操作
        Step 3: allow_rules — 只读命令自动放行
        Step 4: ask_user   — 其余操作询问用户
        """
        # TODO Week 2: 实现真实的 4 步管道
        return PermissionDecision(
            behavior="allow",
            reason="[stub] Week 1 全部放行",
            risk_level="LOW",
        )

    def _is_blacklisted(self, cmd: str) -> bool:
        """检查是否在绝对黑名单中。"""
        for pattern in ABSOLUTE_BLACKLIST:
            if pattern in cmd:
                return True
        return False

    def _classify_risk(self, cmd: str) -> str:
        """根据命令前缀判断风险等级。"""
        for prefix in READ_PREFIXES:
            if cmd.startswith(prefix):
                return "LOW"
        for prefix in HIGH_RISK_PREFIXES:
            if cmd.startswith(prefix):
                return "HIGH"
        return "MEDIUM"
