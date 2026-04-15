#!/usr/bin/env python3
"""
hooks/pre_tool/04_snapshot_hook.py — 执行前快照 Hook

exit 0: 继续执行（无需快照）
exit 2: 注入快照路径到上下文（stdout 内容注入 LLM 对话）

Week 1 stub: 直接 exit 0。
Week 6: 对 HIGH/CRITICAL 操作创建快照，exit 2 注入快照路径。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

# TODO Week 6: 快照逻辑
# tool_name = payload.get("tool_name", "")
# tool_input = payload.get("tool_input", {})

sys.exit(0)
