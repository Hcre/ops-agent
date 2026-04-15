#!/usr/bin/env python3
"""
hooks/pre_tool/02_blacklist_check.py — 绝对黑名单检查 Hook

exit 0: 继续执行
exit 1: 阻断（命中黑名单）

Week 1 stub: 直接 exit 0。
Week 2: 检查 tool_input 中的命令是否在 ABSOLUTE_BLACKLIST 中。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

# TODO Week 2: 从 config.py 导入 ABSOLUTE_BLACKLIST 并检查
# tool_input = payload.get("tool_input", {})
# cmd = tool_input.get("command", "")

sys.exit(0)
