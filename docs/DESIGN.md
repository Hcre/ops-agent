# OpsAgent 设计文档 v2.1

> **项目**：面向 Linux 企业运维的安全 AI Agent 脚手架  
> **模型**：DeepSeek-R1 / Qwen3（OpenAI 兼容接口）  
> **架构基础**：learn-claude-code **s01-s19** 体系  
>
> | 版本 | 变更摘要 |
> |------|---------|
> | v1.0 | 初稿，基于 s01-s12 |
> | v1.1 | 补充持久化 / 回滚 / 熔断机制 |
> | v2.0 | 对齐 s01-s19；Hook 外置安全管道；新增 MemoryManager / CronScheduler / ErrorRecovery / MCPPlugin |
> | **v2.1** | **补充 s00 系列桥接文档洞察：QueryState 控制平面、ToolUseContext 总线、TaskRecord vs RuntimeTaskState 区分、消息管道分层** |

---

## 一、核心哲学

> "Agent 是模型，不是框架。Harness 的职责是让模型的能力得以安全、可追溯地释放。"  
> "安全是一条管道，不是一个布尔值。"——s07  
> "query loop 不只是 while True，而是拿着一份跨轮状态不断推进的查询控制平面。"——s00a

---

## 二、s01-s19 思想具象化映射

### 第一层：感知-推理-行动基础环路（s01-s06）

| OpsAgent 模块 | s 编号 | 原始概念 | 在 OpsAgent 中的具象化 |
|--------------|--------|---------|----------------------|
| `core/agent_loop.py` | **s01** | Agent Loop | `LoopState`（QueryState）驱动；`transition_reason` 显式化每次继续原因 |
| `tools/registry.py` + `tools/mcp_router.py` | **s02** | Tool Use | 原生工具 + MCP 工具统一注册；`ToolUseContext` 总线传递共享环境 |
| `managers/task_manager.py` | **s03** | TodoWrite | 6 态工作图任务状态机（TaskRecord）；与 s12 合并实现 |
| `core/subagent.py` | **s04** | Subagent | Analyst/Executor 完全隔离上下文，执行层不持有分析层历史 |
| `skills/` | **s05** | Skill Loading | 运维手册按需加载；system prompt 只列标题，内容通过工具调用注入 |
| `core/context_manager.py` | **s06** | Context Compact | 三策略：大输出持久化 + micro_compact + auto_compact（LLM 摘要）|

### 第二层：控制与扩展（s07-s11）

| OpsAgent 模块 | s 编号 | 原始概念 | 在 OpsAgent 中的具象化 |
|--------------|--------|---------|----------------------|
| `security/permission_manager.py` | **s07** | Permission System | 4步管道：deny→mode→allow→ask；三种 mode（default/plan/auto）|
| `core/hook_manager.py` + `hooks/` | **s08** | Hook System | PreToolUse/PostToolUse/SessionStart；安全逻辑外置为脚本，exit 0/1/2 合约 |
| `core/memory_manager.py` | **s09** | Memory System | 跨 Session 记忆（admin 偏好 / 已知危险路径 / 事件经验）；DreamConsolidator 7门检查 |
| `core/system_prompt.py` | **s10** | System Prompt | 分层组装：PromptParts（稳定层）+ system-reminder（动态层）；消息管道与 prompt 管道分离 |
| `core/error_recovery.py` | **s11** | Error Recovery | 三策略：max_tokens续写 / prompt_too_long压缩 / 网络指数退避 |

### 第三层：任务与调度（s12-s14）

| OpsAgent 模块 | s 编号 | 原始概念 | 在 OpsAgent 中的具象化 |
|--------------|--------|---------|----------------------|
| `managers/task_manager.py` | **s12** | Task System | `TaskRecord`（工作图，持久化）；与 s03 合并实现 |
| `core/background.py` | **s13** | Background Tasks | `RuntimeTaskState`（执行槽位，纯内存）；OS 后台监控线程 |
| `core/cron_scheduler.py` | **s14** | Cron Scheduler | 5字段 cron；session-only / durable 两种持久化；错过任务检测 |

### 第四层：团队与自治（s15-s18）

| OpsAgent 模块 | s 编号 | 原始概念 | 在 OpsAgent 中的具象化 |
|--------------|--------|---------|----------------------|
| `teams/` | **s15** | Agent Teams | Analyst / Executor / Auditor 三角色；MessageBus 协作 |
| `teams/protocols.py` | **s16** | Team Protocols | 安全审批握手；shutdown_requests / plan_requests FSM |
| `core/autonomous.py` | **s17** | Autonomous Agents | 夜间巡检模式；idle_poll；防止压缩后角色漂移的身份重注入 |
| `core/isolation.py` | **s18** | Worktree Isolation | op_id 绑定；ops_events.jsonl 事件溯源；操作隔离沙箱 |

### 第五层：外部集成（s19）

| OpsAgent 模块 | s 编号 | 原始概念 | 在 OpsAgent 中的具象化 |
|--------------|--------|---------|----------------------|
| `tools/mcp_client.py` + `tools/plugin_loader.py` | **s19** | MCP Plugin | stdio MCPClient；6层能力模型（Config/Transport/Connection/Capability/Auth/Router）；所有 MCP 工具走同一权限门 |

### 第六层：安全护栏（OpsAgent 专属）

| OpsAgent 模块 | 职责 |
|--------------|------|
| `security/intent_classifier.py` | 规则引擎 + UNKNOWN → LLM 审查；混合意图分类 |
| `security/prompt_injection.py` | 提示词注入检测 |
| `security/privilege_broker.py` | 最小权限代理执行（ops-reader uid=9001 / ops-writer uid=9002）|
| `managers/state_store.py` | SQLite 持久化（tasks / circuit_state / snapshots）|
| `core/circuit_breaker.py` | 连续失败熔断；CLOSED → HALF_OPEN → OPEN 状态机 |
| `rollback/` | 三级快照补偿（L1文件 / L2配置 / L3服务）|

---

## 三、v2.1 关键数据结构更新（来自 s00 系列桥接文档）

### 3.1 LoopState = QueryState（s00a / s00c）

`LoopState` 不只是 `messages + turn_count`，必须包含完整的**流程控制状态**：

```python
@dataclass
class LoopState:
    # 内容状态
    messages:              list[dict]   # LLM 对话历史
    session_id:            str

    # 流程控制状态（不要塞进 messages）
    turn_count:            int   = 0
    continuation_count:    int   = 0    # max_tokens 续写次数
    has_attempted_compact: bool  = False
    transition_reason:     str | None = None  # 必须显式赋值
    permission_mode:       str   = "default"
    stop_hook_active:      bool  = False

# TransitionReason 枚举（s00c）
TRANSITIONS = (
    "tool_result_continuation",  # 正常：工具执行完
    "max_tokens_recovery",       # 恢复：输出截断
    "compact_retry",             # 恢复：压缩后重试
    "transport_retry",           # 恢复：网络退避后重试
    "stop_hook_continuation",    # 控制：hook 要求继续
)
```

### 3.2 ToolUseContext 总线（s02a）

工具执行不只是 `handler(tool_input)`，而是通过共享总线访问运行时环境：

```python
@dataclass
class ToolUseContext:
    handlers:        dict                 # tool_name → handler
    permission_mgr:  PermissionManager
    hook_mgr:        HookManager
    broker:          PrivilegeBroker
    auditor:         AuditLogger
    snapshot:        Snapshot
    breaker:         CircuitBreaker
    messages:        list[dict]           # 只读引用
    notifications:   list[str]            # hook exit 2 注入的消息
    cwd:             str = "."
```

### 3.3 TaskRecord vs RuntimeTaskState（s13a）

**两种"任务"不能混用**：

| 类型 | 类名 | 存储 | 管什么 |
|------|------|------|--------|
| 工作图任务 | `TaskRecord` | SQLite（持久化）| 工作目标、依赖、认领状态 |
| 运行时任务 | `RuntimeTaskState` | 内存（运行时）| 当前执行槽位、输出文件、通知状态 |

一个 `TaskRecord` 可以派生多个 `RuntimeTaskState`（如：后台跑测试 + 启动子 Agent）。

### 3.4 消息管道分层（s10a）

system prompt 不是模型完整输入的全部，真正的输入是三条并列管道：

```
PromptParts（稳定层，可缓存）
  - core / tools / skills / memory / CLAUDE.md

NormalizedMessages（消息流）
  - user messages / assistant messages / tool_results / injected reminders

system-reminder（动态层，每轮注入）
  - 当前 OS 感知快照 / 任务状态摘要 / 权限模式
```

---

## 四、安全管道（v2.0 架构，v2.1 不变）

```
用户输入
    ▼ [SessionStart Hook] 加载 memory + 初始化 circuit_breaker
    ▼ [IntentClassifier] 规则引擎 → UNKNOWN → LLM 安全审查员
    ▼ [PermissionManager] deny → mode → allow → ask（三种 mode）
    ▼ LLM 推理（DeepSeek / Qwen3）
    │
    对每个 tool_call：
    ├── [PreToolUse Hooks]  exit 1=阻断 / exit 2=注入消息
    ├── [PermissionManager.check()]  deny / ask / allow
    ├── [PrivilegeBroker]  ops-writer(uid=9002) 降权执行
    └── [PostToolUse Hooks]  审计写入 + 熔断检查
    │
    ▼ [CircuitBreaker.record()]
    ▼ [ErrorRecovery]  三策略兜底
```

---

## 五、完整项目目录结构

```
ops-agent/
├── main.py                    # REPL 入口
├── config.py                  # AgentConfig + MODEL_PROFILES
├── .env                       # API_KEY / BASE_URL / MODEL_ID
├── requirements.txt
├── CLAUDE.md                  # Claude Code 上下文（目录速查 + 关键约束）
│
├── docs/                      # 技术文档
│   ├── DESIGN.md              # 本文件
│   ├── DOC-1-security-layer.md
│   ├── DOC-2-state-resilience.md
│   ├── DOC-3-rollback-audit.md
│   ├── DOC-4-main-loop.md
│   ├── IMPLEMENTATION_PLAN.md
│   └── LOONGARCH_COMPAT.md
│
├── core/                      # Harness 核心层
│   ├── agent_loop.py          # [s01] LoopState + ToolUseContext
│   ├── subagent.py            # [s04]
│   ├── context_manager.py     # [s06]
│   ├── hook_manager.py        # [s08]
│   ├── memory_manager.py      # [s09]
│   ├── system_prompt.py       # [s10] PromptParts + system-reminder
│   ├── error_recovery.py      # [s11]
│   ├── background.py          # [s13] RuntimeTaskState
│   ├── cron_scheduler.py      # [s14]
│   ├── autonomous.py          # [s17]
│   ├── isolation.py           # [s18]
│   └── circuit_breaker.py
│
├── hooks/                     # [s08] 外置安全脚本
│   ├── pre_tool/
│   │   ├── 01_injection_check.py
│   │   ├── 02_blacklist_check.py
│   │   ├── 03_risk_validator.py
│   │   └── 04_snapshot_hook.py
│   └── post_tool/
│       ├── 01_audit_logger.py
│       └── 02_circuit_check.py
│
├── perception/                # OS 感知层
│   ├── aggregator.py
│   ├── disk_monitor.py
│   ├── process_monitor.py
│   ├── network_monitor.py
│   └── log_monitor.py
│
├── security/                  # 安全护栏层
│   ├── permission_manager.py  # [s07]
│   ├── intent_classifier.py
│   ├── prompt_injection.py
│   ├── privilege_broker.py
│   └── rules/intent_rules.yaml
│
├── tools/                     # 工具注册层
│   ├── registry.py            # build_tool_pool()
│   ├── mcp_client.py          # [s19]
│   ├── mcp_router.py
│   ├── plugin_loader.py
│   ├── read_tools.py
│   ├── write_tools.py
│   └── exec_tools.py
│
├── managers/                  # 状态管理层
│   ├── state_store.py         # SQLite WAL
│   ├── task_manager.py        # TaskRecord + RuntimeTaskState
│   └── audit_logger.py        # 8-phase JSONL
│
├── rollback/
│   ├── snapshot.py
│   ├── compensations.py
│   └── recovery.py
│
├── teams/                     # [s15-s16]
│   ├── analyst.py
│   ├── executor.py
│   └── protocols.py
│
├── skills/
│   ├── disk-cleanup/SKILL.md
│   ├── process-management/SKILL.md
│   └── log-analysis/SKILL.md
│
├── .hooks.json                # Hook 配置
├── .claude/scheduled_tasks.json
├── .memory/                   # 运行时生成
├── .audit/                    # 运行时生成
├── .snapshots/                # 运行时生成
├── ops_agent.db               # 运行时生成
│
└── tests/
    ├── conftest.py
    ├── test_permission_manager.py
    ├── test_hook_manager.py
    ├── test_memory_manager.py
    ├── test_cron_scheduler.py
    ├── test_error_recovery.py
    ├── test_intent_classifier.py
    ├── test_privilege_broker.py
    ├── test_state_store.py
    ├── test_task_manager.py
    ├── test_circuit_breaker.py
    ├── test_snapshot.py
    ├── test_recovery.py
    ├── test_audit_logger.py
    ├── test_mcp_client.py
    └── test_agent_loop.py
```

---

## 六、国产模型接入

```python
# config.py
MODEL_PROFILES = {
    "deepseek-chat": {           # 工具调用用这个（R1 不支持 function calling）
        "model_id":  "deepseek-chat",
        "base_url":  "https://api.deepseek.com/v1",
        "context_limit": 64000,
    },
    "deepseek-r1": {             # 纯推理/分析任务用这个
        "model_id":  "deepseek-reasoner",
        "base_url":  "https://api.deepseek.com/v1",
        "supports_thinking": True,
        "context_limit": 64000,
    },
    "qwen3-235b": {
        "model_id":  "qwen3-235b-a22b",
        "base_url":  "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_thinking": True,
        "context_limit": 128000,
    },
}

SECURITY_REVIEWER_MODEL = "qwen3-8b"   # 安全审查员用轻量模型

ERROR_RECOVERY = {
    "max_recovery_attempts": 3,
    "backoff_base_delay":    1.0,
    "backoff_max_delay":     30.0,
    "token_threshold":       50000,
}
```

---

## 七、决策记录

| 决策点 | 选择 | 原因 |
|-------|------|------|
| 框架 | 纯自研 | 需求3（安全校验）和需求4（最小权限）是核心卖点，框架无法实现 |
| 实现策略 | 安全接口优先的增量开发 | 每周可 demo + 接口稳定不返工 |
| 模型 | DeepSeek-chat（工具调用）/ R1（推理）| R1 不支持 function calling |
| 持久化 | SQLite WAL 模式 | 单节点够用，读写不互斥 |
| 权限隔离 | setuid（ops-reader/writer）| OS 级隔离，框架触碰不到 |
| 安全逻辑位置 | Hook 外置脚本 | 不侵入主循环，可独立测试 |
| 审计格式 | 8-phase JSONL | 结构化，支持 op_id 查询完整链路 |
| 任务模型 | TaskRecord + RuntimeTaskState 分离 | 工作目标与执行槽位不是同一层（s13a）|
| 工具上下文 | ToolUseContext 总线 | 工具不应各自偷拿全局变量（s02a）|
