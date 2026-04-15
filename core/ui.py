"""
core/ui.py — OpsAgent 终端 UI 组件

基于 rich + prompt_toolkit 实现：
  - 启动 banner（OpsAgent 品牌）
  - REPL 输入/输出美化（prompt_toolkit 处理中文 IME 输入）
  - 工具执行追踪（实时状态行）
  - Hook 执行可视
  - 安全决策可视（IntentClassifier / PermissionManager）
  - LoopState 状态面板
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner, SPINNERS
from rich.table import Table
from rich.text import Text
from rich import box



if TYPE_CHECKING:
    from core.agent_loop import LoopState, ToolResult
    from security.intent_classifier import IntentResult
    from security.permission_manager import PermissionDecision
    from security.prompt_injection import InjectionResult

# ---------------------------------------------------------------------------
# prompt_toolkit session（全局单例，持有输入历史）
# ---------------------------------------------------------------------------
_pt_session: PromptSession = PromptSession(
    history=InMemoryHistory(),
    # 让 prompt_toolkit 接管终端行编辑，正确处理中文 IME 退格
    mouse_support=False,
)


# ---------------------------------------------------------------------------
# 全局 Console（stderr=False，输出到 stdout）
# ---------------------------------------------------------------------------

console = Console(highlight=False)

# ---------------------------------------------------------------------------
# 颜色主题
# ---------------------------------------------------------------------------

THEME = {
    "brand":    "bold cyan",
    "prompt":   "bold green",
    "answer":   "white",
    "tool":     "bold blue",
    "hook":     "bold magenta",
    "security": "bold yellow",
    "success":  "bold green",
    "warning":  "bold yellow",
    "error":    "bold red",
    "dim":      "dim white",
    "critical": "bold red on dark_red",
}
THEME.update({
    "thinking": "dim cyan",
})

# ---------------------------------------------------------------------------
# 启动 Banner
# ---------------------------------------------------------------------------

BANNER_ART = r"""
  ___  ____  ____    _    ____  _____ _   _ _____
 / _ \|  _ \/ ___|  / \  / ___|| ____| \ | |_   _|
| | | | |_) \___ \ / _ \| |  _ |  _| |  \| | | |
| |_| |  __/ ___) / ___ \ |_| || |___| |\  | | |
 \___/|_|   |____/_/   \_\____||_____|_| \_| |_|
"""


def print_banner(model_id: str, permission_mode: str, session_id: str) -> None:
    """打印启动 banner。"""
    import os
    cwd = os.getcwd()

    art = Text(BANNER_ART, style="bold cyan")
    console.print(art)

    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column(style="white")
    info.add_row("Model",       model_id)
    info.add_row("Permissions", _mode_badge(permission_mode))
    info.add_row("Directory",   cwd)
    info.add_row("Session",     session_id[:16] + "...")

    console.print(info)
    console.print()
    console.print(
        "  输入 [bold]/help[/bold] 查看命令  ·  "
        "[dim]exit[/dim] 退出  ·  "
        "[dim]/mode <default|plan|auto>[/dim] 切换权限模式",
        highlight=False,
    )
    console.print()


def _mode_badge(mode: str) -> Text:
    colors = {"default": "yellow", "plan": "cyan", "auto": "green"}
    color = colors.get(mode, "white")
    return Text(mode, style=f"bold {color}")


# ---------------------------------------------------------------------------
# 生成状态追踪
# ---------------------------------------------------------------------------


class PulseStatus:
    def __init__(self):
        # 核心色调：使用统一的橙红色，不加 dim 也不加 bold
        self.main_color = "#cc4c00"
        
        # 跃动图标序列
        self.frames = ["●", "•", "·", " ", "·", "•", "●", "*", "✱", "❊", "❈", "❊", "✱", "*"]
        
        # 慢速切换的单词
        self.words_cycle = ["thinking", "coalescing", "reasoning", "analyzing"]
        
        # 3次跃动换一个词
        self.pulses_per_word = 3 

    def __rich__(self):
        # 1. 基础时钟计算
        fps = 12
        total_frames = int(time.monotonic() * fps)
        
        # 2. 计算图标位置
        frame_idx = total_frames % len(self.frames)
        icon = self.frames[frame_idx]
        
        # 3. 计算单词循环（每 3 次脉冲换一个词）
        total_cycles = total_frames // len(self.frames)
        word_idx = (total_cycles // self.pulses_per_word) % len(self.words_cycle)
        word = self.words_cycle[word_idx]
        
        # 4. 统一渲染：图标和文字使用完全一样的样式
        # 不再使用 dim，也不再判断是否变亮，保持视觉上的高度统一
        return Text(f" {icon} {word}...", style=self.main_color)

@contextmanager
def generation_status():
    """
    终端 UI：
    - 图标跃动感（圆点 <-> 星星）
    - 文字慢速切换
    - 全体色调统一，视觉平滑不闪烁
    """
    with Live(
        PulseStatus(), 
        console=console, 
        transient=True, 
        refresh_per_second=15
    ):
        yield
# ---------------------------------------------------------------------------
# REPL 输入/输出
# ---------------------------------------------------------------------------

def print_prompt() -> None:
    """仅用于非 prompt_toolkit 路径的兼容占位，正常不调用。"""
    pass


async def async_prompt() -> str:
    """异步读取用户输入，由 prompt_toolkit 处理终端行编辑。

    正确处理中文 IME 的退格/删除，支持上下键历史。
    必须在 asyncio 事件循环中调用（不用 run_in_executor）。
    """
    return await _pt_session.prompt_async(HTML("<ansigreen><b>></b></ansigreen> "))


def print_answer(text: str) -> None:
    """打印 Agent 回答。"""
    console.print()
    console.print(
        Panel(
            Text(text, style="white"),
            title="[bold cyan]OpsAgent[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print()


def print_error(msg: str) -> None:
    console.print(f"[bold red]✗ 错误:[/bold red] {escape(msg)}")


def print_info(msg: str) -> None:
    console.print(f"[dim]ℹ {escape(msg)}[/dim]")


def print_mode_change(mode: str) -> None:
    console.print(f"[bold]权限模式:[/bold] {_mode_badge(mode)}")


# ---------------------------------------------------------------------------
# 安全决策可视
# ---------------------------------------------------------------------------

def print_injection_result(result: "InjectionResult") -> None:
    """显示注入检测结果（非 CLEAN 时才显示）。"""
    if result.verdict == "CLEAN":
        return
    icon = "🚫" if result.verdict == "INJECTED" else "⚠️"
    color = THEME["error"] if result.verdict == "INJECTED" else THEME["warning"]
    label = "注入阻断" if result.verdict == "INJECTED" else "可疑输入"
    console.print(
        f"[{color}]{icon} [SECURITY/{label}][/{color}] "
        f"Layer {result.layer} · score={result.score:.1f} · {escape(result.reason)}"
    )


def print_intent_result(result: "IntentResult") -> None:
    """显示意图分类结果。LOW 静默，MEDIUM 以上才显示。"""
    if result.risk_level == "LOW":
        return
    risk_colors = {
        "MEDIUM":   "yellow",
        "HIGH":     "bold yellow",
        "CRITICAL": "bold red",
        "UNKNOWN":  "magenta",
    }
    color = risk_colors.get(result.risk_level, "white")
    console.print(
        f"[{THEME['security']}]🔍 [Intent][/{THEME['security']}] "
        f"[{color}]{result.risk_level}[/{color}] · "
        f"{escape(result.intent)} · "
        f"[dim]{escape(result.reason[:60])}[/dim] "
        f"[dim]({result.classifier})[/dim]"
    )


def print_permission_decision(
    tool_name: str, decision: "PermissionDecision"
) -> None:
    """显示 PermissionManager 决策。"""
    icons = {"allow": "✓", "ask": "?", "deny": "✗"}
    colors = {"allow": "green", "ask": "yellow", "deny": "red"}
    icon = icons.get(decision.behavior, "·")
    color = colors.get(decision.behavior, "white")
    console.print(
        f"[{THEME['security']}]🔐 [Permission][/{THEME['security']}] "
        f"[{color}]{icon} {decision.behavior.upper()}[/{color}] · "
        f"[bold]{escape(tool_name)}[/bold] · "
        f"[dim]{escape(decision.reason)}[/dim]"
    )


# ---------------------------------------------------------------------------
# 工具执行追踪
# ---------------------------------------------------------------------------

@contextmanager
def tool_execution_tracker(tool_name: str, tool_args: dict):
    """上下文管理器：显示工具执行的实时状态。

    用法：
        with tool_execution_tracker("bash", {"command": "df -h"}) as tracker:
            result = await execute()
            tracker.set_result(result)
    """
    tracker = _ToolTracker(tool_name, tool_args)
    tracker.start()
    try:
        yield tracker
        tracker.finish()
    except Exception as e:
        tracker.fail(str(e))
        raise


class _ToolTracker:
    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        self._t0 = 0.0
        self._result: ToolResult | None = None

    def start(self) -> None:
        self._t0 = time.monotonic()
        # 截断参数显示
        args_preview = str(self.tool_args)[:60]
        console.print(
            f"[{THEME['tool']}]⚙ [Tool][/{THEME['tool']}] "
            f"[bold]{escape(self.tool_name)}[/bold] "
            f"[dim]{escape(args_preview)}[/dim] "
            f"[dim]→ running...[/dim]",
            end="\r",
        )

    def set_result(self, result: "ToolResult") -> None:
        self._result = result

    def finish(self) -> None:
        elapsed = (time.monotonic() - self._t0) * 1000
        r = self._result
        if r and r.success:
            preview = escape(r.output[:80].replace("\n", " "))
            console.print(
                f"[{THEME['tool']}]⚙ [Tool][/{THEME['tool']}] "
                f"[bold]{escape(self.tool_name)}[/bold] "
                f"[{THEME['success']}]✓[/{THEME['success']}] "
                f"[dim]{elapsed:.0f}ms[/dim] "
                f"[dim]→ {preview}[/dim]"
            )
        elif r:
            console.print(
                f"[{THEME['tool']}]⚙ [Tool][/{THEME['tool']}] "
                f"[bold]{escape(self.tool_name)}[/bold] "
                f"[{THEME['error']}]✗[/{THEME['error']}] "
                f"[dim]{elapsed:.0f}ms[/dim] "
                f"[{THEME['error']}]{escape(r.error[:80])}[/{THEME['error']}]"
            )

    def fail(self, reason: str) -> None:
        elapsed = (time.monotonic() - self._t0) * 1000
        console.print(
            f"[{THEME['tool']}]⚙ [Tool][/{THEME['tool']}] "
            f"[bold]{escape(self.tool_name)}[/bold] "
            f"[{THEME['error']}]✗ exception[/{THEME['error']}] "
            f"[dim]{elapsed:.0f}ms[/dim] "
            f"[{THEME['error']}]{escape(reason[:80])}[/{THEME['error']}]"
        )


# ---------------------------------------------------------------------------
# Hook 执行可视
# ---------------------------------------------------------------------------

def print_hook_start(event: str, script: str) -> None:
    console.print(
        f"[{THEME['hook']}]🪝 [Hook/{event}][/{THEME['hook']}] "
        f"[dim]{escape(script)}[/dim] "
        f"[dim]→ running...[/dim]",
        end="\r",
    )


def print_hook_result(
    event: str,
    script: str,
    exit_code: int,
    elapsed_ms: float,
    stdout: str = "",
) -> None:
    """显示 Hook 执行结果。"""
    if exit_code == 0:
        status = f"[{THEME['success']}]✓ exit 0[/{THEME['success']}]"
    elif exit_code == 1:
        status = f"[{THEME['error']}]✗ exit 1 (阻断)[/{THEME['error']}]"
    elif exit_code == 2:
        status = f"[{THEME['warning']}]↩ exit 2 (注入消息)[/{THEME['warning']}]"
    else:
        status = f"[{THEME['error']}]? exit {exit_code}[/{THEME['error']}]"

    line = (
        f"[{THEME['hook']}]🪝 [Hook/{event}][/{THEME['hook']}] "
        f"[dim]{escape(script.split('/')[-1])}[/dim] "
        f"{status} [dim]{elapsed_ms:.0f}ms[/dim]"
    )
    if stdout and exit_code in (1, 2):
        line += f" [dim]→ {escape(stdout[:60])}[/dim]"
    console.print(line)


# ---------------------------------------------------------------------------
# LoopState 状态面板（/status 命令用）
# ---------------------------------------------------------------------------

def print_loop_state(state: "LoopState") -> None:
    """打印 LoopState 详细状态面板。"""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", min_width=20)
    grid.add_column(style="white")

    grid.add_row("session_id",          state.session_id[:16] + "...")
    grid.add_row("turn_count",          str(state.turn_count))
    grid.add_row("continuation_count",  str(state.continuation_count))
    grid.add_row("permission_mode",     _mode_badge(state.permission_mode))
    grid.add_row("transition_reason",   state.transition_reason or "[dim]None[/dim]")
    grid.add_row("has_attempted_compact", str(state.has_attempted_compact))
    grid.add_row("stop_hook_active",    str(state.stop_hook_active))
    grid.add_row("active_task_id",      state.active_task_id or "[dim]None[/dim]")
    grid.add_row("subagent_depth",      str(state.subagent_depth))
    grid.add_row("messages (count)",    str(len(state.messages)))

    console.print(
        Panel(grid, title="[bold cyan]LoopState[/bold cyan]", border_style="cyan")
    )


# ---------------------------------------------------------------------------
# 确认提示
# ---------------------------------------------------------------------------

async def confirm(prompt: str) -> bool:
    """异步确认提示，由 prompt_toolkit 处理输入，返回 True/False。"""
    try:
        ans = await _pt_session.prompt_async(
            HTML(f"<ansiyellow><b>{prompt}</b></ansiyellow> <ansiwhite>[y/N]</ansiwhite> ")
        )
        return ans.strip().lower() == "y"
    except (EOFError, KeyboardInterrupt, KeyError):
        return False


def print_confirm_request(
    tool_name: str,
    risk_level: str,
    reason: str,
    snap_path: str | None = None,
) -> None:
    """显示高危操作确认请求。"""
    lines = [
        f"[bold]工具:[/bold] {escape(tool_name)}",
        f"[bold]风险:[/bold] [{THEME['error']}]{risk_level}[/{THEME['error']}]",
        f"[bold]原因:[/bold] {escape(reason)}",
    ]
    if snap_path:
        lines.append(f"[bold]快照:[/bold] [dim]{escape(snap_path)}[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold red]⚠ 高危操作确认[/bold red]",
            border_style="red",
        )
    )
