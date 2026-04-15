#!/usr/bin/env python3
"""
hooks/post_tool/02_circuit_check.py — 熔断状态检查 Hook

exit 0: 继续执行
exit 1: 熔断器 OPEN，阻断后续调用

Week 1 stub: 直接 exit 0。
Week 5: 读取 SQLite 熔断状态，OPEN 时 exit 1。
"""
import json
import os
import sys

payload_str = os.environ.get("HOOK_PAYLOAD", "{}")
try:
    payload = json.loads(payload_str)
except json.JSONDecodeError:
    payload = {}

# TODO Week 5: 检查熔断器状态
# import sqlite3
# conn = sqlite3.connect("ops_agent.db")
# ...

sys.exit(0)
