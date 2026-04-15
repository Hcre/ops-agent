"""
core/system_prompt.py — SystemPromptBuilder

对应 s10 System Prompt + s10a 消息管道分层

三条并列管道（s10a）：
  PromptParts（稳定层，可缓存）  ← 本文件的 _get_stable_parts()
  NormalizedMessages（消息流）   ← agent_loop 维护的 state.messages，不在此处理
  system-reminder（动态层）      ← 本文件的 build_reminder()，每轮重新生成

调用方式：
  system_prompt = builder.build(state, perception)
  # 等价于：stable_parts + "\n\n" + build_reminder(state, perception)
  # stable_parts 在首次调用后缓存，不重复构建
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AgentConfig
    from core.agent_loop import LoopState


# ---------------------------------------------------------------------------
# PromptParts 稳定层（可缓存，不随轮次变化）
# ---------------------------------------------------------------------------

CORE_PROMPT = """\
你是 OpsAgent，一个面向 Linux 企业运维的安全 AI Agent。

## 核心原则
- 安全第一：任何破坏性操作必须经过确认
- 最小权限：只申请完成任务所需的最低权限
- 可追溯：所有操作都有审计记录
- 透明：向管理员清晰说明每一步操作的目的和风险

## 能力范围
- 磁盘/进程/网络/日志的查看与分析
- 系统清理、服务管理（需确认）
- 配置文件查看与修改（需确认）
- 故障诊断与根因分析

## 禁止行为
- 不执行绝对黑名单中的命令（rm -rf /、fork bomb 等）
- 不绕过权限检查
- 不在未确认的情况下执行高危操作
"""

TOOL_USAGE_PROMPT = """\
## 工具使用规范
- 优先使用只读工具收集信息，再决定是否需要写操作
- 每次工具调用前，在思考中说明目的和预期结果
- 工具执行失败时，分析原因后再决定是否重试
"""


# ---------------------------------------------------------------------------
# SystemPromptBuilder
# ---------------------------------------------------------------------------

class SystemPromptBuilder:
    """组装三层 system prompt。

    稳定层（PromptParts）：首次构建后缓存，不随轮次变化。
    动态层（system-reminder）：每轮调用 build() 时重新生成，注入当前状态。
    消息流（NormalizedMessages）：由 agent_loop 维护的 state.messages，不在此处理。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._stable_cache: str | None = None   # 稳定层缓存

    def build(self, state: "LoopState", perception: dict) -> str:
        """构建完整 system prompt = 稳定层 + 动态层。

        稳定层只构建一次（可被 LLM API 的 prompt cache 命中）。
        动态层每轮重新生成，反映当前 OS 状态和任务进度。
        """
        stable = self._get_stable_parts()
        reminder = self.build_reminder(state, perception)
        return f"{stable}\n\n{reminder}"

    def build_reminder(self, state: "LoopState", perception: dict) -> str:
        """动态层（system-reminder）：每轮重新生成。

        感知数据（disk/load）只在 turn_count==1 时注入 system-reminder。
        后续轮次的感知变化通过 hook exit 2 注入到 messages，不重复污染 system prompt。
        这样避免多轮对话后 system prompt 里堆积 N 份快照。
        """
        lines = ["## 当前运行状态 [system-reminder]"]
        lines.append(f"- 权限模式: {state.permission_mode}")
        lines.append(f"- 会话轮次: {state.turn_count}")

        # 感知快照只在第一轮注入（后续变化走 hook exit 2 → messages）
        if perception and state.turn_count <= 1:
            lines.append("\n### OS 感知快照（初始）")
            if "disk" in perception:
                lines.append(f"- 磁盘: {perception['disk']}")
            if "load" in perception:
                lines.append(f"- 负载: {perception['load']}")

        # 告警始终注入（无论哪轮，告警都要让模型看到）
        if perception and perception.get("alerts"):
            lines.append("\n### 当前告警")
            for alert in perception["alerts"]:
                lines.append(f"- ⚠️ {alert}")

        # Week 5+: 注入任务状态摘要
        # task_summary = self._task_mgr.summary()
        # if task_summary:
        #     lines.append(f"\n### 任务状态\n{task_summary}")

        return "\n".join(lines)

    def invalidate_cache(self) -> None:
        """当 skills/memory 变化时，主动失效稳定层缓存。"""
        self._stable_cache = None

    def _get_stable_parts(self) -> str:
        """稳定层：首次构建后缓存。包含 core + tools + skills + memory。"""
        if self._stable_cache is None:
            parts = [CORE_PROMPT, TOOL_USAGE_PROMPT]
            # Week 3+: 注入已加载的 skill 标题列表
            # skill_index = self._build_skill_index()
            # if skill_index:
            #     parts.append(skill_index)
            # Week 7+: 注入跨 Session 记忆摘要
            # memory_summary = self._memory_mgr.summary()
            # if memory_summary:
            #     parts.append(memory_summary)
            self._stable_cache = "\n\n".join(p.strip() for p in parts)
        return self._stable_cache
