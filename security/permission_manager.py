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
        """4 步决策管道。

        Step 1: deny_rules — 绝对黑名单
        Step 2: mode_check — plan 模式拒绝写操作
        Step 3: allow_rules — 只读命令自动放行
        Step 4: ask_user   — 其余操作询问用户
        """
        # 提取命令（仅支持 exec_bash）
        if tool_name != "exec_bash":
            return PermissionDecision(
                behavior="allow",
                reason=f"Non-bash tool: {tool_name}",
                risk_level="LOW",
            )

        cmd = tool_args.get("cmd", "").strip()
        if not cmd:
            return PermissionDecision(
                behavior="deny",
                reason="Empty command",
                risk_level="LOW",
            )

        # Step 1: 绝对黑名单检查
        if self._is_blacklisted(cmd):
            return PermissionDecision(
                behavior="deny",
                reason=f"Command in absolute blacklist",
                risk_level="CRITICAL",
            )

        # 分类风险等级
        risk_level = self._classify_risk(cmd)

        # Step 2: plan 模式检查（仅允许只读）
        if self._mode == "plan":
            if risk_level != "LOW":
                return PermissionDecision(
                    behavior="deny",
                    reason="Plan mode: write operations not allowed",
                    risk_level=risk_level,
                )

        # Step 3: 只读命令自动放行
        if risk_level == "LOW":
            return PermissionDecision(
                behavior="allow",
                reason="Read-only command",
                risk_level="LOW",
            )

        # Step 4: 其余操作询问用户
        # 在 auto 模式下，非高危写操作自动放行
        if self._mode == "auto" and risk_level == "MEDIUM":
            return PermissionDecision(
                behavior="allow",
                reason="Auto mode: medium-risk operation auto-allowed",
                risk_level="MEDIUM",
            )

        # 默认询问用户
        return PermissionDecision(
            behavior="ask",
            reason=f"User confirmation required for {risk_level} operation",
            risk_level=risk_level,
        )

    def _is_blacklisted(self, cmd: str) -> bool:
        """检查是否在绝对黑名单中。"""
        for pattern in ABSOLUTE_BLACKLIST:
            if pattern in cmd:
                return True
        return False

    def _classify_risk(self, cmd: str) -> str:
        """根据命令前缀判断风险等级。"""
        cmd_stripped = cmd.strip()
        for prefix in READ_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "LOW"
        for prefix in HIGH_RISK_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "HIGH"
        return "MEDIUM"
