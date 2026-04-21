"""
core/ui_interface.py — UI 抽象接口

定义 AgentLoop 与 UI 层之间的契约。
终端实现：core/ui.py（TerminalUI）
未来 B/S 实现：WebUIHandler（websocket.emit）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_loop import LoopState, ToolResult
    from security.intent_classifier import IntentResult
    from security.permission_manager import PermissionDecision
    from security.prompt_injection import InjectionResult


class UIInterface(ABC):
    """AgentLoop 依赖的 UI 抽象接口。

    Loop 只调用这里定义的方法，不直接依赖 rich / websocket 等具体实现。
    """

    # ------------------------------------------------------------------
    # 启动与基础输出
    # ------------------------------------------------------------------

    @abstractmethod
    def print_banner(self, model_id: str, permission_mode: str, session_id: str) -> None: ...

    @abstractmethod
    def print_answer(self, text: str) -> None: ...

    @abstractmethod
    def print_error(self, msg: str) -> None: ...

    @abstractmethod
    def print_info(self, msg: str) -> None: ...

    @abstractmethod
    def print_mode_change(self, mode: str) -> None: ...

    # ------------------------------------------------------------------
    # 流式思考（DeepSeek-R1 reasoning_content）
    # ------------------------------------------------------------------

    @abstractmethod
    def start_thought(self) -> None:
        """LLM 开始输出思维链，初始化流式显示"""
        ...

    @abstractmethod
    def update_thought(self, chunk: str) -> None:
        """追加一段思维链文本"""
        ...

    @abstractmethod
    def stop_thought(self) -> None:
        """思维链输出结束，收尾显示"""
        ...

    # ------------------------------------------------------------------
    # 安全决策可视
    # ------------------------------------------------------------------

    @abstractmethod
    def print_injection_result(self, result: "InjectionResult") -> None: ...

    @abstractmethod
    def print_intent_result(self, result: "IntentResult") -> None: ...

    @abstractmethod
    def print_permission_decision(
        self, tool_name: str, tool_input: dict, decision: "PermissionDecision"
    ) -> None: ...

    # ------------------------------------------------------------------
    # 工具执行追踪
    # ------------------------------------------------------------------

    @abstractmethod
    def tool_execution_tracker(self, tool_name: str, tool_args: dict): ...

    @abstractmethod
    def generation_status(self): ...

    # ------------------------------------------------------------------
    # Hook 可视
    # ------------------------------------------------------------------

    @abstractmethod
    def print_hook_start(self, event: str, script: str) -> None: ...

    @abstractmethod
    def print_hook_result(
        self, event: str, script: str, exit_code: int,
        elapsed_ms: float, stdout: str = ""
    ) -> None: ...

    # ------------------------------------------------------------------
    # 状态与确认
    # ------------------------------------------------------------------

    @abstractmethod
    def print_loop_state(self, state: "LoopState") -> None: ...

    @abstractmethod
    async def async_prompt(self) -> str: ...

    @abstractmethod
    async def confirm(self, prompt: str) -> bool: ...

    @abstractmethod
    def print_confirm_request(
        self, tool_name: str, risk_level: str,
        reason: str, snap_path: str | None = None
    ) -> None: ...
