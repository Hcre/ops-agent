"""
core/hook_manager.py — Hook 系统管理器

对应 s08 Hook System
外置安全脚本通过 subprocess 调用，exit code 合约：
  0 = 继续执行
  1 = 阻断工具调用
  2 = 注入上下文消息（stdout 内容注入 LLM 对话）
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AgentConfig


@dataclass
class HookResult:
    blocked: bool = False
    block_reason: str = ""
    messages: list[str] = field(default_factory=list)   # exit 2 注入的消息
    permission_override: str | None = None               # hook 可覆盖权限决策


class HookManager:
    """管理 PreToolUse / PostToolUse / SessionStart 三类 Hook。

    每个 Hook 脚本通过 subprocess 调用，exit code 合约：
      0 → 继续
      1 → 阻断（block_reason = stdout）
      2 → 注入消息（messages += stdout 行）
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._hooks: dict[str, list[dict]] = {}
        self._load_config()

    def _load_config(self) -> None:
        path = self._config.hooks_config
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._hooks = data.get("hooks", {})
        except Exception as e:
            from core import ui
            ui.print_error(f"[HookManager] 加载 {path} 失败: {e}")

    async def run_hooks(
        self,
        event: str,
        payload: dict | None = None,
    ) -> HookResult:
        """运行指定事件的所有 Hook，返回聚合结果。

        匹配逻辑：
          - hook 有 matcher 字段时，只在 payload["tool_name"] 匹配时执行
          - matcher 为 "*" 或 null 时匹配所有工具
        """
        from core import ui

        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookResult()

        tool_name = (payload or {}).get("tool_name", "")
        result = HookResult()

        for hook in hooks:
            matcher = hook.get("matcher")
            if matcher and matcher != "*" and matcher != tool_name:
                continue

            script = hook.get("command", "")
            ui.print_hook_start(event, script)

            exit_code, stdout, elapsed_ms = await self._run_hook_async(
                hook, payload or {}
            )

            ui.print_hook_result(event, script, exit_code, elapsed_ms, stdout)

            if exit_code == 1:
                result.blocked = True
                result.block_reason = stdout or f"Hook 阻断: {script}"
                break  # 第一个阻断即停止后续 hook

            if exit_code == 2:
                # stdout 每行作为一条注入消息
                for line in stdout.splitlines():
                    line = line.strip()
                    if line:
                        result.messages.append(line)

            # hook 可通过 stdout JSON 覆盖权限决策
            if exit_code == 0 and stdout.startswith("{"):
                try:
                    data = json.loads(stdout)
                    if "permissionDecision" in data:
                        result.permission_override = data["permissionDecision"]
                except json.JSONDecodeError:
                    pass

        return result

    async def _run_hook_async(
        self, hook: dict, payload: dict
    ) -> tuple[int, str, float]:
        """异步执行单个 Hook 脚本，返回 (exit_code, stdout, elapsed_ms)。"""
        t0 = time.monotonic()
        exit_code, stdout = await asyncio.get_event_loop().run_in_executor(
            None, self._run_single_hook, hook, payload
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        return exit_code, stdout, elapsed_ms

    def _run_single_hook(
        self, hook: dict, payload: dict
    ) -> tuple[int, str]:
        """同步执行单个 Hook 脚本，返回 (exit_code, stdout)。"""
        cmd = hook.get("command", "")
        env = {**os.environ, "HOOK_PAYLOAD": json.dumps(payload)}
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            return proc.returncode, proc.stdout.strip()
        except subprocess.TimeoutExpired:
            return 1, "Hook 超时（>10s）"
        except Exception as e:
            return 1, f"Hook 执行异常: {e}"

    def set_mode(self, mode: str) -> None:
        """同步权限模式到环境变量，Hook 脚本可读取 AGENT_MODE。"""
        os.environ["AGENT_MODE"] = mode
