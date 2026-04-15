#!/usr/bin/env python3
"""
hooks/pre_tool/03_risk_validator.py — 危险参数校验 Hook

exit 0: 继续执行
exit 1: 阻断（HIGH 风险且未确认）
exit 2: 注入警告消息（stdout 内容注入 LLM 对话）

Week 1 stub: 直接 exit 0。
Week 2: 校验命令参数风险等级，HIGH 时 exit 2 注入警告。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

# TODO Week 2: 风险校验逻辑
# tool_name = payload.get("tool_name", "")
# tool_input = payload.get("tool_input", {})

sys.exit(0)
