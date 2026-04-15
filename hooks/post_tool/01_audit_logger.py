#!/usr/bin/env python3
"""
hooks/post_tool/01_audit_logger.py — 审计日志 Hook

always exit 0（审计失败不阻断主流程）

Week 1 stub: 打印到 stderr，不写文件。
Week 4: 写 8-phase JSONL 审计记录。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

tool_name = payload.get("tool_name", "unknown")
# TODO Week 4: 写 JSONL 审计记录到 .audit/ 目录
print(f"[audit stub] tool={tool_name}", file=sys.stderr)

sys.exit(0)
