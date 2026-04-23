"""
security/intent_classifier.py — 意图分类器

定位：用户体验层的提前预警，不是安全层的最终防线。

职责：
  - 用正则扫描输入中的高危操作信号（MEDIUM/HIGH/CRITICAL）
  - 命中 → 返回 IntentResult，触发用户确认或早期预警
  - 未命中 → 返回 None，直接放行给 Agent LLM 处理

不做的事：
  - 不判断是否是"闲聊"（闲聊白名单是以有穷对抗无穷）
  - 不处理 UNKNOWN（不确定就放行，后续 PermissionManager + Hook 保底）
  - 不做跨轮上下文分析（tool_call 层已有保底）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from config import AgentConfig

RiskLevel = Literal["MEDIUM", "HIGH", "CRITICAL"]
CmdCategory = Literal["read", "file", "service", "unknown"]


@dataclass
class CommandRiskResult:
    """tool_call 层的结构化审查结果，由 classify_command() 输出。"""
    risk_level:       Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason:           str
    blast_radius:     str
    reversible:       bool
    needs_human:      bool
    suggested_action: str
    classifier:       Literal["rule", "llm", "default"]
    category:         CmdCategory = "read"   # 决定路由到哪个 ops-* 账号
    context:          str = "default"        # 预留多租户扩展，默认 "default"

    @property
    def target_user(self) -> str:
        """组合成最终的 OS 账号名，供 PrivilegeBroker 使用。"""
        if self.context == "default":
            return f"ops-{self.category}"
        return f"ops-{self.category}-{self.context}"


@dataclass
class IntentResult:
    risk_level: RiskLevel
    intent: str
    reason: str
    matched_pattern: str = ""
    classifier: Literal["rule"] = "rule"


@dataclass
class _CompiledRule:
    id: str
    description: str
    intent: str
    risk_level: RiskLevel
    patterns: list[re.Pattern] = field(default_factory=list)


class IntentClassifier:
    """高危操作信号扫描器（正则引擎）。

    只返回 MEDIUM/HIGH/CRITICAL，未命中返回 None（放行）。
    规则从 intent_rules.yaml 热加载，pattern 支持正则表达式。
    规则按 CRITICAL → HIGH → MEDIUM 顺序匹配，高风险优先。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._rules: list[_CompiledRule] = []
        self._load_rules()

    def _load_rules(self) -> None:
        rules_path = os.path.join(
            os.path.dirname(__file__), "rules", "intent_rules.yaml"
        )
        if not os.path.exists(rules_path):
            return
        try:
            with open(rules_path) as f:
                data = yaml.safe_load(f)

            raw_rules = [
                r for r in data.get("rules", [])
                if r.get("risk_level") in ("MEDIUM", "HIGH", "CRITICAL")
            ]

            # 按风险等级排序：CRITICAL → HIGH → MEDIUM（高风险优先命中）
            order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
            raw_rules.sort(key=lambda r: order.get(r["risk_level"], 9))

            compiled = []
            for rule in raw_rules:
                patterns = []
                for p in rule.get("patterns", []):
                    try:
                        patterns.append(re.compile(p, re.IGNORECASE))
                    except re.error as e:
                        from core import ui
                        ui.print_error(f"[IntentClassifier] 规则 {rule['id']} pattern 编译失败: {p!r} — {e}")
                compiled.append(_CompiledRule(
                    id=rule["id"],
                    description=rule["description"],
                    intent=rule["intent"],
                    risk_level=rule["risk_level"],
                    patterns=patterns,
                ))
            self._rules = compiled

        except Exception as e:
            from core import ui
            ui.print_error(f"[IntentClassifier] 加载规则失败: {e}")

    async def classify(self, user_input: str) -> IntentResult | None:
        """扫描输入中的高危操作信号。

        返回 None         → 未检测到高危信号，直接放行
        返回 IntentResult → 检测到 MEDIUM/HIGH/CRITICAL，触发对应处理
        """
        # 对原始输入和规范化输入都扫描，取最高风险
        normalized = self._normalize(user_input)
        result_raw = self._scan(user_input)
        result_norm = self._scan(normalized) if normalized != user_input else None

        if result_raw and result_norm:
            order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
            return result_raw if order[result_raw.risk_level] <= order[result_norm.risk_level] else result_norm
        return result_raw or result_norm

    def _normalize(self, text: str) -> str:
        """规范化输入，消除常见的 shell 引号/空白绕过手法。

        例如：
          rm -r'/'f /  →  rm -rf /
          r''m -rf /   →  rm -rf /
          /et''c/      →  /etc/
        """
        # 移除单引号包裹的空字符串（'', ""）
        text = re.sub(r"''|\"\"", "", text)
        # 移除单引号包裹的单个字符后再拼接（如 r'/'f → rf）
        text = re.sub(r"'(.)'", r"\1", text)
        # 移除多余空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _scan(self, user_input: str) -> IntentResult | None:
        """正则引擎扫描，CRITICAL → HIGH → MEDIUM 优先级顺序。"""
        for rule in self._rules:
            for pattern in rule.patterns:
                m = pattern.search(user_input)
                if m:
                    return IntentResult(
                        risk_level=rule.risk_level,
                        intent=rule.intent,
                        reason=f"规则 {rule.id}: {rule.description}",
                        matched_pattern=pattern.pattern,
                    )
        return None

    def reload_rules(self) -> None:
        """热重载规则文件（无需重启）。"""
        self._rules = []
        self._load_rules()
