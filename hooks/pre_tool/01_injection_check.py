#!/usr/bin/env python3
"""
hooks/pre_tool/01_injection_check.py — 提示词注入检测 Hook

exit 0: 继续执行
exit 1: 阻断（检测到注入）

Week 1 stub: 直接 exit 0。
Week 2: 调用 security/prompt_injection.py 检测 tool_input。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

# TODO Week 2: 检测 payload["tool_input"] 中的注入特征
# tool_input = payload.get("tool_input", {})

sys.exit(0)
