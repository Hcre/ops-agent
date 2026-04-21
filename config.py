from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 模型 Profiles（支持 DeepSeek / Qwen3）
# ---------------------------------------------------------------------------

MODEL_PROFILES: dict[str, dict] = {
    "deepseek-r1": {
        # ⚠️ deepseek-reasoner 不支持 function calling，自动路由到 deepseek-chat
        "model_id": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "supports_thinking": False,  # deepseek-chat 不输出 <think>
        "context_limit": 64000,
        "max_tools_per_request": 32,
    },
    "deepseek-reasoner": {
        # 思维链模式，不支持 tools，仅用于分析类子任务
        "model_id": "deepseek-reasoner",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "supports_thinking": True,
        "supports_tools": False,  # 显式标记不支持
        "context_limit": 64000,
        "max_tools_per_request": 0,
    },
    "qwen3-235b": {
        "model_id": "qwen3-235b-a22b",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "supports_thinking": True,
        "context_limit": 128000,
        "max_tools_per_request": 128,
    },
    "qwen3-8b": {
        "model_id": "qwen3-8b",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "supports_thinking": False,
        "context_limit": 32000,
        "max_tools_per_request": 64,
    },
}

# ---------------------------------------------------------------------------
# 运行模式
# ---------------------------------------------------------------------------

RunMode = Literal["default", "plan", "auto"]
"""
default: 读操作允许，写操作询问用户
plan:    仅允许只读（零写入风险，运维排查模式）
auto:    读操作自动放行，非高危写操作自动放行
"""


# ---------------------------------------------------------------------------
# Agent 主配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentConfig:
    # 模型
    model_profile: str = field(
        default_factory=lambda: os.getenv("MODEL_PROFILE", "deepseek-r1")
    )
    security_reviewer_model: str = field(
        default_factory=lambda: os.getenv("SECURITY_REVIEWER_MODEL", "qwen3-8b")
    )

    # 运行模式
    mode: RunMode = field(
        default_factory=lambda: os.getenv("AGENT_MODE", "default")  # type: ignore
    )

    # 主循环限制
    max_turns: int = 50
    max_recovery_attempts: int = 3

    # 错误恢复（s11）
    backoff_base_delay: float = 1.0   # 秒
    backoff_max_delay: float = 30.0   # 秒
    token_threshold: int = 50000      # chars/4 ≈ tokens

    # 熔断器（OpsAgent）
    circuit_fail_threshold: int = 3   # 连续失败次数触发 OPEN
    circuit_reset_timeout: int = 60   # OPEN → HALF_OPEN 等待秒数

    # 快照（OpsAgent）
    snapshot_max_size_mb: int = 100   # 超过则仅保存元数据 + diff
    snapshot_retention_hours: int = 24

    # 上下文压缩（s06）
    context_large_output_threshold: int = 10000  # chars
    context_micro_compact_threshold: float = 0.7  # 上下文窗口使用率

    # 感知（s01 感知端）
    perception_timeout_s: float = 2.0

    # SQLite 数据库
    db_path: str = "ops_agent.db"

    # 路径
    hooks_config: str = ".hooks.json"
    memory_dir: str = ".memory"
    audit_dir: str = ".audit"
    snapshot_dir: str = ".snapshots"
    tasks_dir: str = ".tasks"
    skills_dir: str = "skills"

    def get_model_profile(self) -> dict:
        if self.model_profile not in MODEL_PROFILES:
            raise ValueError(
                f"Unknown model profile: {self.model_profile}. "
                f"Available: {list(MODEL_PROFILES.keys())}"
            )
        return MODEL_PROFILES[self.model_profile]

    def get_api_key(self) -> str:
        profile = self.get_model_profile()
        key = os.getenv(profile["api_key_env"], "")
        if not key:
            raise RuntimeError(
                f"Environment variable {profile['api_key_env']} is not set. "
                "Please copy .env.example to .env and fill in your API key."
            )
        return key


# ---------------------------------------------------------------------------
# 错误恢复配置（s11）
# ---------------------------------------------------------------------------

ERROR_RECOVERY = {
    "max_recovery_attempts": 3,
    "backoff_base_delay": 1.0,
    "backoff_max_delay": 30.0,
    "token_threshold": 50000,
}

# ---------------------------------------------------------------------------
# 安全配置（OpsAgent 专属）
# ---------------------------------------------------------------------------

# 绝对黑名单（硬编码，不可通过 YAML 配置覆盖）
ABSOLUTE_BLACKLIST: list[str] = [
    "rm -rf /",
    "rm -rf /*",
    "dd if=/dev/zero of=/dev/",
    "dd if=/dev/urandom of=/dev/",
    "chmod -R 777 /",
    "chmod -R 000 /",
    ":(){ :|:& };:",  # fork bomb
    "> /dev/sda",
    "mkfs.",
]

# 只读命令前缀（risk=read，自动放行）
READ_PREFIXES: list[str] = [
    # 目录/路径
    "pwd", "cd",
    # 磁盘/文件系统
    "df", "du", "ls", "cat", "head", "tail", "less", "more",
    "find", "locate", "stat", "file", "wc", "md5sum", "sha256sum",
    "readlink", "realpath", "basename", "dirname",
    # 进程/系统
    "ps", "top", "htop", "pgrep", "pstree",
    "free", "uptime", "who", "w", "last", "lastlog",
    "uname", "hostname", "date", "timedatectl", "cal",
    "id", "whoami", "groups", "ulimit",
    # 网络（只读）
    "netstat", "ss", "lsof", "ip addr", "ip route", "ip link",
    "ifconfig", "ping", "traceroute", "nslookup", "dig", "host",
    "curl", "wget",
    # 日志/服务状态
    "journalctl", "dmesg",
    "systemctl status", "systemctl list", "systemctl is-",
    "service --status",
    # 文本处理
    "grep", "egrep", "fgrep", "awk", "sed", "sort", "uniq",
    "cut", "tr", "echo", "printf", "xargs",
    # 包/环境信息
    "which", "whereis", "type", "env", "printenv",
    "dpkg -l", "dpkg -s", "rpm -q", "apt list",
    "python", "python3", "pip", "pip3",
    # 其他只读
    "man", "help", "history", "alias",
]

# 高危命令前缀（risk=high，必须确认）
HIGH_RISK_PREFIXES: list[str] = [
    "rm", "rmdir", "shred",
    "dd", "mkfs", "fdisk", "parted",
    "mv", "cp",  # 覆盖场景
    "chmod", "chown", "chgrp",
    "systemctl stop", "systemctl disable", "systemctl kill",
    "kill", "killall", "pkill",
    "iptables", "ufw",
    "passwd", "useradd", "userdel", "usermod",
    "crontab",
]

# 最小权限执行用户
PRIVILEGE_USERS = {
    "reader": {"uid": 9001, "username": "ops-reader"},
    "writer": {"uid": 9002, "username": "ops-writer"},
}
