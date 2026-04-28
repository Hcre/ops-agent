"""
security/permission_manager.py — 权限管理器

职责：
  - 接收 IntentClassifier 输出的 CommandRiskResult
  - 依据运行模式（default / plan / auto）做 allow/ask/deny 最终决策

不做的事：
  - 不做命令分类（由 IntentClassifier 负责）
  - 不做风险扫描（由 IntentClassifier 负责）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from config import AgentConfig
    from security.intent_classifier import CommandRiskResult

DecisionBehavior = Literal["allow", "ask", "deny"]


@dataclass
class PermissionDecision:
    behavior: DecisionBehavior
    reason: str
    risk_level: str = "LOW"


class PermissionManager:
    """工具调用权限决策器。

    决策矩阵：

    | risk_level | default  | plan     | auto     |
    |-----------|----------|----------|----------|
    | LOW       | allow    | allow    | allow    |
    | MEDIUM    | ask      | deny     | allow    |
    | HIGH      | ask      | deny     | ask      |
    | CRITICAL  | deny     | deny     | deny     |

    软化规则：
    - reversible==True 且 risk_level==HIGH → 可降级为 ask（即使 plan 模式）
    - needs_human==True → 强制 ask（即使是 auto 模式）
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._mode: str = config.mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def check(
        self, tool_name: str, tool_args: dict,
        risk_result: "CommandRiskResult | None" = None,
    ) -> PermissionDecision:
        """权限决策入口。

        支持两种调用方式：
        1. 新方式（推荐）：传入 risk_result，做纯决策
        2. 旧方式（兼容）：不传 risk_result，仅做基础检查
           （无分类结果时，对非 exec_bash 放行，exec_bash 默认 ask）
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

        # 无分类结果时回退到保守策略
        if risk_result is None:
            return PermissionDecision(
                behavior="ask",
                reason="无风险分类结果，保守要求用户确认",
                risk_level="MEDIUM",
            )

        return self._decide(risk_result)

    def _decide(self, r: "CommandRiskResult") -> PermissionDecision:
        """基于 CommandRiskResult 和当前运行模式做最终决策。"""
        risk = r.risk_level

        # CRITICAL → 无条件拒绝
        if risk == "CRITICAL":
            return PermissionDecision(
                behavior="deny",
                reason=r.reason,
                risk_level="CRITICAL",
            )

        # HIGH — plan 模式拒绝（除非 reversible 软化）
        if risk == "HIGH":
            if self._mode == "plan" and not r.reversible:
                return PermissionDecision(
                    behavior="deny",
                    reason=f"Plan mode: {r.reason}",
                    risk_level="HIGH",
                )
            # reversible 软化：HIGH → ask
            return PermissionDecision(
                behavior="ask",
                reason=r.reason,
                risk_level="HIGH",
            )

        # MEDIUM — 按模式分流
        if risk == "MEDIUM":
            if self._mode == "plan":
                return PermissionDecision(
                    behavior="deny",
                    reason=f"Plan mode: {r.reason}",
                    risk_level="MEDIUM",
                )
            if self._mode == "auto" and not r.needs_human:
                return PermissionDecision(
                    behavior="allow",
                    reason=f"Auto mode: {r.reason}",
                    risk_level="MEDIUM",
                )
            return PermissionDecision(
                behavior="ask",
                reason=r.reason,
                risk_level="MEDIUM",
            )

        # LOW → 无条件放行
        return PermissionDecision(
            behavior="allow",
            reason=r.reason,
            risk_level="LOW",
        )
