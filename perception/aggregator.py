"""
perception/aggregator.py — 上下文工程层（ContextBuilder）

职责：把 PerceptionResult 转成注入 LLM system prompt 的文本。
控制 token 预算，按 context 使用率决定注入多少。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .collector import Collector, RawSnapshot
from .filter import PerceptionFilter, PerceptionResult, PerceptionAlert

if TYPE_CHECKING:
    from config import AgentConfig


class ContextBuilder:
    """将 PerceptionResult 转成 system prompt 文本片段"""

    MAX_TOKENS = 800   # 感知摘要最大 token 预算（粗估：字符数 / 3）

    def build_prompt_section(
        self,
        result: PerceptionResult,
        context_usage_ratio: float = 0.0,
    ) -> str:
        """
        生成注入 system prompt 的感知摘要文本。

        context_usage_ratio: 当前 context 使用率（0.0~1.0）
          < 0.70 → 注入所有告警
          0.70~0.80 → 只注入 CRITICAL + HIGH
          0.80~0.85 → 只注入 CRITICAL
          > 0.85 → 停止注入，只保留工具提示
        """
        if not result.alerts:
            return ""

        ts = time.strftime("%H:%M:%S", time.localtime(result.timestamp))

        if context_usage_ratio > 0.85:
            return (
                f"## 系统状态 [{ts}]\n"
                "（context 紧张，感知摘要已省略，请调用 get_system_snapshot 获取当前状态）"
            )

        if context_usage_ratio > 0.80:
            alerts = [a for a in result.alerts if a.level == "CRITICAL"]
        elif context_usage_ratio > 0.70:
            alerts = [a for a in result.alerts if a.level in ("CRITICAL", "HIGH")]
        else:
            alerts = result.alerts

        if not alerts:
            return ""

        lines = [f"## 系统状态 [{ts}]", ""]
        for alert in alerts:
            prefix = {"CRITICAL": "🔴", "HIGH": "🟡", "INFO": "🔵"}[alert.level]
            lines.append(f"{prefix} [{alert.level}] {alert.message}")

        # 汇总推荐工具（去重保序）
        all_tools: list[str] = []
        seen: set[str] = set()
        for a in alerts:
            for t in a.suggested_tools:
                if t not in seen:
                    seen.add(t)
                    all_tools.append(t)

        if all_tools:
            lines.append("")
            lines.append("如需详情，可调用：" + "、".join(f"`{t}`" for t in all_tools))

        text = "\n".join(lines)

        # token 预算截断：只保留 CRITICAL
        if self._estimate_tokens(text) > self.MAX_TOKENS:
            lines = [f"## 系统状态 [{ts}]（已截断，仅显示 CRITICAL）", ""]
            for alert in [a for a in alerts if a.level == "CRITICAL"]:
                lines.append(f"🔴 [CRITICAL] {alert.message}")
            text = "\n".join(lines)

        return text

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 3


# ---------------------------------------------------------------------------
# PerceptionAggregator — 对外统一入口
# ---------------------------------------------------------------------------

class PerceptionAggregator:
    """
    对外统一入口，组合 Collector + PerceptionFilter + ContextBuilder。
    agent_loop 只需调用此类，不直接接触内部三层。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._collector = Collector(config)
        self._filter = PerceptionFilter()
        self._builder = ContextBuilder()
        self._last_snapshot: RawSnapshot | None = None

    async def snapshot(self) -> PerceptionResult:
        """采集 + 过滤，返回 PerceptionResult"""
        raw = await self._collector.collect()
        self._last_snapshot = raw
        return self._filter.process(raw)

    def build_prompt_section(
        self,
        result: PerceptionResult,
        context_usage_ratio: float = 0.0,
    ) -> str:
        """生成注入 system prompt 的文本"""
        return self._builder.build_prompt_section(result, context_usage_ratio)

    def reset_baseline(self) -> None:
        """工具执行后重置基线"""
        self._filter.reset_baseline()

    def get_last_snapshot(self) -> RawSnapshot | None:
        """供感知工具按需查询原始数据"""
        return self._last_snapshot

    # 保留旧接口兼容
    def to_dict(self, result: PerceptionResult) -> dict:
        return {
            "timestamp": result.timestamp,
            "has_change": result.has_change,
            "alerts": [
                {
                    "level": a.level,
                    "category": a.category,
                    "message": a.message,
                    "suggested_tools": a.suggested_tools,
                }
                for a in result.alerts
            ],
        }
