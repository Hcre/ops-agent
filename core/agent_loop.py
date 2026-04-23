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
    consecutive_denials:    int   = 0          # 连续权限拒绝次数，超过阈值强制终止

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

    def __init__(self, config: AgentConfig, registry=None) -> None:
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

        # 工具注册表：优先使用外部传入的（含 MCP 工具），否则新建
        if registry is None:
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
        # --- 官方建议：每个新问题开始前，清理历史消息中的推理内容 ---
        self._clear_reasoning_history(state.messages)
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
            usage_ratio = min(len(state.messages) / max(self.config.max_turns * 10, 1), 1.0)
            section = self._perception_agg.build_prompt_section(result, usage_ratio)
            # 调试：显示感知结果
            if result.alerts:
                for alert in result.alerts:
                    self._ui.print_info(
                        f"👁 [Perception/{alert.level}] {alert.message}"
                    )
            return section
        except Exception as e:
            self._ui.print_error(f"感知失败: {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 3: LLM 推理（含 tool_use 循环）
    # ------------------------------------------------------------------

    async def _phase3_reason(self, state: LoopState, perception: str) -> str:
        """调用 LLM，处理 tool_use 循环，返回最终文本回答。流式输出思维链。"""
        ctx = self._build_tool_use_context(state)
        system_prompt = self._prompt_builder.build(state, perception)

        # tool_call 适配器（定义在循环外，避免重复创建类）
        class _FakeFunction:
            def __init__(self, name: str, arguments: str) -> None:
                self.name = name
                self.arguments = arguments

        class _FakeToolCall:
            def __init__(self, d: dict) -> None:
                self.id = d["id"]
                self.function = _FakeFunction(
                    d["function"]["name"],
                    d["function"]["arguments"],
                )

        while state.turn_count < self.config.max_turns:
            state.turn_count += 1

            full_reasoning = ""
            full_content   = ""
            tool_calls_raw = []
            finish_reason  = "stop"

            # 流式调用：思维链实时显示，工具调用等完整响应
            self._ui.start_thought()
            thought_started = False

            async for chunk in await self._llm_call_stream(system_prompt, state.messages):
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                # reasoning_content：DeepSeek-R1 思维链
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    if not thought_started:
                        thought_started = True
                    full_reasoning += rc
                    self._ui.update_thought(rc)

                # content：正式回答
                if delta.content:
                    if thought_started:
                        self._ui.stop_thought()
                        thought_started = False
                    full_content += delta.content

                # tool_calls：工具调用（累积，不流式处理）
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        while len(tool_calls_raw) <= idx:
                            tool_calls_raw.append({
                                "id": "", "type": "function",
                                "function": {"name": "", "arguments": ""}
                            })
                        if tc_delta.id:
                            tool_calls_raw[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_raw[idx]["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_raw[idx]["function"]["arguments"] += tc_delta.function.arguments

                if chunk.choices and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            # 确保思维链 UI 已关闭
            if thought_started:
                self._ui.stop_thought()

            # 将 assistant 消息加入历史
            assistant_msg: dict = {"role": "assistant", "content": full_content}
            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning
            if tool_calls_raw:
                assistant_msg["tool_calls"] = tool_calls_raw
            state.messages.append(assistant_msg)

            if finish_reason in ("stop", "end_turn"):
                return full_content

            if finish_reason == "tool_calls":
                # 连续拒绝熔断：超过 3 次强制终止，避免 LLM 死循环
                if state.consecutive_denials >= 3:
                    state.consecutive_denials = 0
                    return (
                        "操作已被安全系统连续拒绝，无法继续执行。"
                        "请检查权限模式（/mode）或换一种方式描述需求。"
                    )

                fake_calls = [_FakeToolCall(d) for d in tool_calls_raw]
                await self._phase4_execute(fake_calls, ctx, state)
                state.transition_reason = "tool_result_continuation"
                continue

            if finish_reason == "length":
                state.continuation_count += 1
                state.transition_reason = "max_tokens_recovery"
                state.messages.append({"role": "user", "content": "请继续你的回答。"})
                continue

            return full_content

        return "[错误] 超过最大轮次限制。"

    # ------------------------------------------------------------------
    # Phase 4: 安全执行
    # ------------------------------------------------------------------

    async def _phase4_execute(
        self, tool_calls: list, ctx: ToolUseContext, state: LoopState | None = None
    ) -> None:
        """处理所有 tool_call，结果直接追加到 ctx.messages。"""
        for tc in tool_calls:
            result = await self._handle_single_tool(tc, ctx)
            ctx.messages.append(result.to_llm_message())

            # 统计连续拒绝次数，用于熔断
            if state is not None:
                if not result.success and ("权限拒绝" in result.error or "需要用户确认" in result.error):
                    state.consecutive_denials += 1
                else:
                    state.consecutive_denials = 0  # 成功执行则重置

            # 工具执行后重置感知基线
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
        raw_arguments = tool_call.function.arguments or ""

        # 参数解析失败：直接返回终止性错误，不让 LLM 重试空参数
        try:
            tool_args = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=f"工具参数 JSON 解析失败，请检查参数格式: {e}",
            )

        # exec_bash 空命令：直接拒绝，不走权限检查避免无意义循环
        if tool_name == "exec_bash" and not tool_args.get("cmd", "").strip():
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error="exec_bash 需要非空的 cmd 参数",
            )

        # PermissionManager 权限检查（可视化决策）
        decision = ctx.permission_mgr.check(tool_name, tool_args)
        self._ui.print_permission_decision(tool_name, tool_args, decision) # 传入 tool_args

        if decision.behavior == "deny":
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                output="",
                error=f"权限拒绝: {decision.reason}",
            )

        if decision.behavior == "ask":
            # 暂停推理，等待用户终端确认（DOC-7 遗留问题 #6）
            confirmed = await self._ui.confirm_tool_execution(tool_name, tool_args, decision)
            if not confirmed:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_name,
                    success=False,
                    output="",
                    error=f"用户拒绝执行: {decision.reason}",
                )
            # 用户确认后继续往下执行，不 return

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
            with self._ui.tool_execution_tracker(tool_name, tool_args) as tracker:
                result = await handler(**tool_args)
            elapsed_ms = (time.monotonic() - t0) * 1000

            # 工具可能返回 ToolResult 或原始字符串
            if isinstance(result, ToolResult):
                tracker.set_result(result)
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
        from tools.exec_tools import _broker
        return ToolUseContext(
            handlers=self._tool_handlers,
            permission_mgr=self._perm_mgr,
            hook_mgr=self._hook_mgr,
            messages=state.messages,
            notifications=[],
            task_mgr=self._task_mgr,
            error_recovery=self._error_recovery,
            perception_agg=self._perception_agg,
            broker=_broker,
        )

    async def _llm_call(self, system_prompt: str, messages: list[dict]):
        """调用 LLM（非流式，保留供兼容）。"""
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs: dict = {
            "model": self._model_id,
            "messages": full_messages,
        }
        if self._tool_schemas:
            kwargs["tools"] = self._tool_schemas
            kwargs["tool_choice"] = "auto"
        return await self._client.chat.completions.create(**kwargs)

    async def _llm_call_stream(self, system_prompt: str, messages: list[dict]):
        """调用 LLM（流式），返回 async generator。"""
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs: dict = {
            "model": self._model_id,
            "messages": full_messages,
            "stream": True,
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

    def _clear_reasoning_history(self, messages: list):
        """对应官方 clear_reasoning_content 建议"""
        for msg in messages:
            if "reasoning_content" in msg:
                # 官方示例里是设为 None，或者直接 pop 掉
                msg.pop("reasoning_content", None)