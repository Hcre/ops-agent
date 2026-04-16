"""
core/agent_loop.py — OpsAgent 主循环

对应 s01 Agent Loop + s00a QueryState + s02a ToolUseContext
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from openai import AsyncOpenAI

from config import AgentConfig, MODEL_PROFILES

if TYPE_CHECKING:
    from core.hook_manager import HookManager
    from core.system_prompt import SystemPromptBuilder
    from security.intent_classifier import IntentClassifier, IntentResult
    from security.permission_manager import PermissionManager
    from security.prompt_injection import PromptInjectionDetector
    from managers.task_manager import TaskManager
    from perception.aggregator import PerceptionAggregator
    from core.error_recovery import ErrorRecovery

# ---------------------------------------------------------------------------
# QueryState / LoopState（s00a / s00c）
# ---------------------------------------------------------------------------

# 每次继续循环的原因（必须显式赋值，不能只写 continue）
TRANSITIONS = (
    "tool_result_continuation",   # 正常：工具执行完，继续推理
    "max_tokens_recovery",        # 恢复：输出截断，注入续写消息
    "compact_retry",              # 恢复：上下文压缩后重试
    "transport_retry",            # 恢复：网络抖动退避后重试
    "stop_hook_continuation",     # 控制：hook 要求本轮不结束
)


@dataclass
class LoopState:
    """主循环运行时状态，对应 s00a QueryState。
    流程控制字段不要塞进 messages。
    """
    messages:               list[dict]
    session_id:             str

    # 流程控制状态
    turn_count:             int   = 0
    continuation_count:     int   = 0          # max_tokens 续写次数
    has_attempted_compact:  bool  = False
    transition_reason:      str | None = None  # 上一轮为什么继续（必须显式赋值）
    permission_mode:        str   = "default"  # default / plan / auto
    stop_hook_active:       bool  = False

    # 任务追踪（Week 5 填充）
    active_task_id:         str | None = None  # 当前 in_progress 的 TaskRecord id

    # 子 Agent 追踪（Week 7 填充）
    subagent_depth:         int   = 0          # 防止无限递归，最大深度 3


# ---------------------------------------------------------------------------
# ToolResult（s02a）— 工具执行的结构化结果
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """工具执行的内部结构化结果。

    在转成 LLM 消息之前，先经过审计/熔断/回滚处理。
    不要直接把字符串塞进 messages，先走这个结构。
    """
    tool_call_id:   str
    tool_name:      str
    success:        bool
    output:         str                     # 给 LLM 看的文本（成功时的 stdout）
    error:          str = ""               # 失败原因（success=False 时填充）
    op_id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    elapsed_ms:     float = 0.0            # 执行耗时（Week 4 审计用）
    exit_code:      int = 0                # 原始退出码（Week 4 审计用）
    
    def to_llm_message(self) -> dict:
        """转成 OpenAI tool message 格式，注入 messages 列表。"""
        content = self.output if self.success else f"[错误] {self.error}"
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": content,
        }


# ---------------------------------------------------------------------------
# ToolUseContext 总线（s02a）
# ---------------------------------------------------------------------------

@dataclass
class ToolUseContext:
    """工具控制平面的共享环境总线。
    所有工具通过此总线访问运行时状态，不直接访问全局变量。
    """
    handlers:       dict                    # tool_name → handler callable
    permission_mgr: "PermissionManager"
    hook_mgr:       "HookManager"
    messages:       list[dict]              # 当前对话历史（只读引用）
    notifications:  list[str]              # hook exit 2 注入的消息
    cwd:            str = "."

    # Week 2: 错误恢复 + 感知 + 任务管理
    error_recovery: "ErrorRecovery" = None
    perception_agg: "PerceptionAggregator" = None
    task_mgr:       "TaskManager" = None

    # Week 3+: MCP 路由器（s19）
    # 所有 MCP 工具走同一权限门，不绕过 permission_mgr
    mcp_router:     object = None           # MCPRouter（Week 3）

    # Week 4+: 审计 + 权限隔离
    broker:         object = None           # PrivilegeBroker（Week 4）
    auditor:        object = None           # AuditLogger（Week 4）

    # Week 5+: 熔断 + 任务
    breaker:        object = None           # CircuitBreaker（Week 5）

    # Week 6+: 快照回滚
    snapshot:       object = None           # Snapshot（Week 6）


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    """OpsAgent 主循环。

    Week 1 实现：
    - 完整的 LoopState + ToolUseContext 骨架
    - 真实 LLM 调用（OpenAI 兼容接口）
    - 安全模块接口调用（stub 实现，只记录日志，不阻断）
    - 工具执行框架（Week 2+ 填充真实工具）
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        profile = config.get_model_profile()

        self._client = AsyncOpenAI(
            api_key=config.get_api_key(),
            base_url=profile["base_url"],
        )
        self._model_id: str = profile["model_id"]
        self._context_limit: int = profile["context_limit"]

        # 安全模块（Week 1 为 stub，Week 2 替换为真实实现）
        from security.prompt_injection import PromptInjectionDetector
        from security.intent_classifier import IntentClassifier
        from security.permission_manager import PermissionManager
        from core.hook_manager import HookManager
        from core.system_prompt import SystemPromptBuilder
        from core import ui
        from tools.registry import ToolRegistry
        from managers.task_manager import TaskManager
        from perception.aggregator import PerceptionAggregator
        from core.error_recovery import ErrorRecovery

        self._injection = PromptInjectionDetector()
        self._intent = IntentClassifier(config)
        self._perm_mgr = PermissionManager(config)
        self._hook_mgr = HookManager(config)
        self._prompt_builder = SystemPromptBuilder(config)
        self._ui = ui

        # Week 2 组件
        self._task_mgr = TaskManager(config)
        self._perception_agg = PerceptionAggregator(config)
        self._error_recovery = ErrorRecovery(config)

        # 工具注册表（Week 2+ 填充）
        registry = ToolRegistry()
        self._tool_handlers = registry.handlers
        self._tool_schemas = registry.get_schemas()

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """启动 REPL 主循环（Phase 0 初始化 + while True）"""
        await self._phase0_init()

        state = LoopState(
            messages=[],
            session_id=str(uuid.uuid4()),
        )

        # REPL 命令注册表：新增命令只需在此注册，不改主循环
        repl_commands: dict = {
            "/mode":   self._cmd_mode,
            "/status": self._cmd_status,
        }

        self._ui.print_banner(self._model_id, state.permission_mode, state.session_id)

        while True:
            # Cron 通知注入（Week 7 填充）
            # for note in self._cron.drain_notifications():
            #     state.messages.append({"role": "user", "content": note})

            try:
                raw = await self._ui.async_prompt()
            except (EOFError, KeyboardInterrupt):
                self._ui.print_info("退出。")
                break

            user_input = raw.strip()
            if not user_input:
                continue
            if user_input == "exit":
                break

            # 斜杠命令分发
            cmd_key = user_input.split()[0]
            if cmd_key in repl_commands:
                args = user_input[len(cmd_key):].strip()
                await repl_commands[cmd_key](args, state)
                continue

            try:
                answer = await self._handle_message(user_input, state)
                self._ui.print_answer(answer)
            except Exception as e:
                self._ui.print_error(str(e))

    # ------------------------------------------------------------------
    # 单条消息处理
    # ------------------------------------------------------------------

    async def _handle_message(self, user_input: str, state: LoopState) -> str:
        """处理单条用户输入，返回最终回答。"""
        # Phase 1: 输入防御
        intent_result = await self._phase1_defend(user_input, state)
        if not intent_result:
            return "输入被安全检查拦截。"

        # Phase 2: 环境感知（Week 3 填充真实感知）
        perception = await self._phase2_perceive(state)

        # 将用户消息加入历史
        state.messages.append({"role": "user", "content": user_input})

        # Phase 3: LLM 推理循环
        answer = await self._phase3_reason(state, perception)

        # Phase 5: 结果归档（Week 4+ 填充）
        await self._phase5_archive(state, answer)

        return answer

    # ------------------------------------------------------------------
    # Phase 1: 输入防御
    # ------------------------------------------------------------------

    async def _phase1_defend(
        self, user_input: str, state: LoopState
    ) -> bool:
        """输入防御：只做注入检测。

        返回 False → 已阻断
        返回 True  → 放行

        意图分类已移至 tool_call 层（PermissionManager），
        用户自然语言层规则库误报率高，无实际价值。
        """
        inj = self._injection.check(user_input)
        self._ui.print_injection_result(inj)
        if inj.verdict == "INJECTED":
            return False
        return True

    # ------------------------------------------------------------------
    # Phase 2: 环境感知
    # ------------------------------------------------------------------

    async def _phase2_perceive(self, state: LoopState) -> str:
        """OS 环境感知，返回注入 system prompt 的文本片段。"""
        try:
            result = await self._perception_agg.snapshot()
            # 粗估 context 使用率：消息数 / (max_turns * 10)
            usage_ratio = min(len(state.messages) / max(self.config.max_turns * 10, 1), 1.0)
            return self._perception_agg.build_prompt_section(result, usage_ratio)
        except Exception as e:
            self._ui.print_error(f"感知失败: {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 3: LLM 推理（含 tool_use 循环）
    # ------------------------------------------------------------------

    async def _phase3_reason(self, state: LoopState, perception: dict) -> str:
        """调用 LLM，处理 tool_use 循环，返回最终文本回答。"""
        ctx = self._build_tool_use_context(state)
        system_prompt = self._prompt_builder.build(state, perception)

        while state.turn_count < self.config.max_turns:
            state.turn_count += 1
              # --- 这里使用 UI 动态图标 ---
            with self._ui.generation_status():
                # 真正的网络请求在 yield 期间执行
                response = await self._llm_call(system_prompt, state.messages)

            #response = await self._llm_call(system_prompt, state.messages)
            choice = response.choices[0]    
            finish_reason = choice.finish_reason

            # 将 assistant 消息加入历史
            assistant_msg = {"role": "assistant"}
            if choice.message.content:
                assistant_msg["content"] = choice.message.content
            if choice.message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]
            state.messages.append(assistant_msg)


            if finish_reason == "stop" or finish_reason == "end_turn":
                return choice.message.content or ""

            if finish_reason == "tool_calls":
                # Phase 4: 安全执行（messages 在 _phase4_execute 内部追加）
                await self._phase4_execute(choice.message.tool_calls, ctx)
                state.transition_reason = "tool_result_continuation"
                continue

            if finish_reason == "length":
                # max_tokens 截断，注入续写消息
                state.continuation_count += 1
                state.transition_reason = "max_tokens_recovery"
                state.messages.append({
                    "role": "user",
                    "content": "请继续你的回答。",
                })
                continue

            # 其他情况直接返回
            return choice.message.content or ""

        return "[错误] 超过最大轮次限制。"

    # ------------------------------------------------------------------
    # Phase 4: 安全执行
    # ------------------------------------------------------------------

    async def _phase4_execute(
        self, tool_calls: list, ctx: ToolUseContext
    ) -> None:
        """处理所有 tool_call，结果直接追加到 ctx.messages。"""
        for tc in tool_calls:
            result = await self._handle_single_tool(tc, ctx)
            ctx.messages.append(result.to_llm_message())
            # 工具执行后重置感知基线，让下次感知能对比到变化
            if ctx.perception_agg is not None:
                ctx.perception_agg.reset_baseline()
            # hook exit 2 注入的消息也追加进去
            for note in ctx.notifications:
                ctx.messages.append({"role": "user", "content": f"[Hook]: {note}"})
            ctx.notifications.clear()

    async def _handle_single_tool(self, tool_call, ctx: ToolUseContext) -> ToolResult:
        """单个 tool_call 的完整安全管道，返回 ToolResult。

        完整管道（Week 2 实现）：
        PermissionManager → 工具执行 → 错误恢复 → 输出检测
        """
        tool_name = tool_call.function.name
        try:
            tool_args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            tool_args = {}

        # Week 2: PermissionManager 权限检查
        decision = ctx.permission_mgr.check(tool_name, tool_args)
        if decision.behavior == "deny":
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=f"权限拒绝: {decision.reason}",
            )

        if decision.behavior == "ask":
            # 实际应该询问用户，这里简化为拒绝
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=f"需要用户确认: {decision.reason}",
            )

        # 查找 handler
        handler = ctx.handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=f"未知工具: {tool_name}",
            )

        t0 = time.monotonic()
        try:
            result = await handler(**tool_args)
            elapsed_ms = (time.monotonic() - t0) * 1000

            # 工具可能返回 ToolResult（exec_bash/read_file/list_dir）或原始字符串
            if isinstance(result, ToolResult):
                if not result.success:
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_name,
                        success=False,
                        output="",
                        error=result.error,
                        elapsed_ms=elapsed_ms,
                        exit_code=result.exit_code,
                    )
                raw_output = result.output
            else:
                raw_output = str(result)

            # 间接注入检测：工具输出可能被攻击者污染（日志/文件内容里埋指令）
            inj = self._injection.check_tool_output(raw_output)
            if inj.verdict == "INJECTED":
                self._ui.print_injection_result(inj)
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_name,
                    success=False,
                    output="",
                    error=f"工具输出被注入检测阻断: {inj.reason}",
                    elapsed_ms=elapsed_ms,
                )

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=True,
                output=raw_output,
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

    # ------------------------------------------------------------------
    # Phase 5: 结果归档
    # ------------------------------------------------------------------

    async def _phase5_archive(self, state: LoopState, answer: str) -> None:
        """完成归档（Week 4+ 填充真实实现）。"""
        pass

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _build_tool_use_context(self, state: LoopState) -> ToolUseContext:
        """每轮构建工具执行上下文总线。"""
        return ToolUseContext(
            handlers=self._tool_handlers,
            permission_mgr=self._perm_mgr,
            hook_mgr=self._hook_mgr,
            messages=state.messages,
            notifications=[],
            task_mgr=self._task_mgr,
            error_recovery=self._error_recovery,
            perception_agg=self._perception_agg,
        )

    async def _llm_call(self, system_prompt: str, messages: list[dict]):
        """调用 LLM（带 system prompt）。"""
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs: dict = {
            "model": self._model_id,
            "messages": full_messages,
        }
        if self._tool_schemas:
            kwargs["tools"] = self._tool_schemas
            kwargs["tool_choice"] = "auto"

        return await self._client.chat.completions.create(**kwargs)

    async def _cli_confirm_intent(self, user_input: str, result) -> bool:
        """CLI 意图确认。"""
        self._ui.print_confirm_request(
            tool_name=result.intent,
            risk_level=result.risk_level,
            reason=result.reason,
        )
        return await self._ui.confirm("确认执行?")

    async def _phase0_init(self) -> None:
        """Phase 0: 启动初始化（Week 5+ 填充持久化恢复）。"""
        pass

    # ------------------------------------------------------------------
    # REPL 命令处理器（注册到 repl_commands 字典）
    # ------------------------------------------------------------------

    async def _cmd_mode(self, args: str, state: LoopState) -> None:
        mode = args.strip()
        if mode in ("default", "plan", "auto"):
            state.permission_mode = mode
            self._perm_mgr.set_mode(mode)
            self._ui.print_mode_change(mode)
        else:
            self._ui.print_error("无效模式，可选: default / plan / auto")

    async def _cmd_status(self, args: str, state: LoopState) -> None:
        self._ui.print_loop_state(state)
