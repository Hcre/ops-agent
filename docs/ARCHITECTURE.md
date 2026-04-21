# OpsAgent v2.0 — 架构图集

> 所有图表使用 Mermaid 语法，可在 GitHub / GitLab / VS Code 预览。

---

## 图 1：系统分层架构（Layer Architecture）

```mermaid
graph TB
    subgraph USER["👤 用户层"]
        CLI["CLI / REPL\nmain.py"]
    end

    subgraph HARNESS["🧠 Harness 核心层 (core/)"]
        LOOP["AgentLoop\nagent_loop.py\n[s01]"]
        CTX["ContextManager\ncontext_manager.py\n[s06]"]
        HOOK["HookManager\nhook_manager.py\n[s08]"]
        MEM["MemoryManager\nmemory_manager.py\n[s09]"]
        SP["SystemPrompt\nsystem_prompt.py\n[s10]"]
        ERR["ErrorRecovery\nerror_recovery.py\n[s11]"]
        CRON["CronScheduler\ncron_scheduler.py\n[s14]"]
        BG["BackgroundMonitor\nbackground.py\n[s13]"]
        CB["CircuitBreaker\ncircuit_breaker.py"]
    end

    subgraph SECURITY["🔒 安全护栏层 (security/)"]
        IC["IntentClassifier\nintent_classifier.py"]
        PM["PermissionManager\npermission_manager.py\n[s07]"]
        PI["PromptInjection\nprompt_injection.py"]
        PB["PrivilegeBroker\nprivilege_broker.py"]
    end

    subgraph HOOKS_DIR["🪝 Hook 脚本层 (hooks/)"]
        PRE["PreToolUse Hooks\n01_injection_check\n02_blacklist_check\n03_risk_validator\n04_snapshot_hook"]
        POST["PostToolUse Hooks\n01_audit_logger\n02_circuit_check"]
    end

    subgraph TOOLS["🔧 工具注册层 (tools/)"]
        REG["Registry\nregistry.py\n[s02]"]
        READ["ReadTools\nread_tools.py"]
        WRITE["WriteTools\nwrite_tools.py"]
        MCP["MCPClient\nmcp_client.py\n[s19]"]
    end

    subgraph PERCEPTION["👁️ OS 感知层 (perception/)"]
        AGG["PerceptionAggregator\naggregator.py"]
        DISK["DiskMonitor"]
        PROC["ProcessMonitor"]
        NET["NetworkMonitor"]
        LOG["LogMonitor"]
    end

    subgraph MANAGERS["💾 状态管理层 (managers/)"]
        SS["StateStore\nstate_store.py (SQLite)"]
        TM["TaskManager\ntask_manager.py\n[s03+s12]"]
        AL["AuditLogger\naudit_logger.py"]
    end

    subgraph ROLLBACK["🔄 回滚补偿层 (rollback/)"]
        SNAP["Snapshot\nsnapshot.py"]
        COMP["Compensations\ncompensations.py"]
        REC["Recovery\nrecovery.py"]
    end

    subgraph LLM["☁️ 外部 LLM"]
        DS["DeepSeek-R1\ndeepseek-chat"]
        QW["Qwen3-235B\nDashScope"]
    end

    CLI --> LOOP
    LOOP --> IC
    IC --> PM
    LOOP --> SP
    SP --> MEM
    SP --> AGG
    LOOP --> LLM
    LLM -->|tool_calls| HOOK
    HOOK --> PRE
    PRE --> PM
    PM --> PB
    PB --> TOOLS
    TOOLS --> POST
    POST --> AL
    POST --> CB
    CB --> SS
    AL --> SS
    TM --> SS
    AGG --> DISK & PROC & NET & LOG
    PRE --> SNAP
    SNAP --> SS
    ERR --> LOOP
    CRON --> LOOP
    BG --> LOOP
```

---

## 图 2：完整请求生命周期时序图（Sequence Diagram）

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as main.py
    participant Loop as AgentLoop
    participant IC as IntentClassifier
    participant PM as PermissionManager
    participant SP as SystemPrompt
    participant Perc as PerceptionAggregator
    participant LLM as DeepSeek/Qwen3
    participant HM as HookManager
    participant Pre as PreToolUse Hooks
    participant Snap as Snapshot
    participant PB as PrivilegeBroker
    participant Post as PostToolUse Hooks
    participant AL as AuditLogger
    participant CB as CircuitBreaker
    participant Mem as MemoryManager

    User->>CLI: "帮我清理系统垃圾"
    CLI->>Loop: run(user_input)

    Note over Loop: [SessionStart Hook] 加载记忆

    Loop->>Mem: load_session_context()
    Mem-->>Loop: {known_protected_paths, admin_prefs}

    Loop->>IC: classify(user_input)
    IC->>IC: 规则引擎匹配 YAML
    alt 规则命中
        IC-->>Loop: {intent: cleanup, risk: MEDIUM}
    else UNKNOWN → LLM 审查
        IC->>LLM: security_review(input)
        LLM-->>IC: {risk: MEDIUM}
        IC-->>Loop: {intent: cleanup, risk: MEDIUM}
    end

    Loop->>PM: mode_check(risk=MEDIUM, mode=default)
    PM-->>Loop: ALLOWED (proceed)

    Loop->>Perc: collect_snapshot()
    Perc-->>Loop: {disk: /var/log/mysql=47GB, procs: [...]}

    Loop->>SP: build(perception, memory, task)
    SP-->>Loop: system_prompt_string

    Loop->>LLM: chat_completion(messages, tools)
    LLM-->>Loop: tool_call(bash, "rm /var/log/mysql/slow.log")

    Note over Loop: 对每个 tool_call 执行 Hook 管道

    Loop->>HM: run_pre_hooks(tool_call)
    HM->>Pre: 01_injection_check.py
    Pre-->>HM: exit 0 ✅

    HM->>Pre: 02_blacklist_check.py
    Pre-->>HM: exit 0 ✅

    HM->>Pre: 03_risk_validator.py
    Pre-->>HM: exit 2 ⚠️ "数据库路径 + rm → 升级 HIGH"
    Note over HM: 注入上下文消息

    HM->>Pre: 04_snapshot_hook.py
    Pre->>Snap: take_snapshot(op_id, path)
    Snap-->>Pre: snapshot_path
    Pre-->>HM: exit 2 ⚠️ "快照已创建: .snapshots/op_x1_..."
    Note over HM: 注入快照路径消息

    HM-->>Loop: pre_hooks_result

    Loop->>PM: check(tool=bash, cmd=rm, risk=HIGH)
    PM->>PM: deny_rules → 不命中
    PM->>PM: mode_check → default 模式，HIGH → ask
    PM->>CLI: prompt_user(confirm=HIGH)
    CLI->>User: ⚠️ HIGH 风险操作确认框
    User->>CLI: y (确认)
    CLI-->>PM: CONFIRMED
    PM-->>Loop: EXECUTE

    Loop->>CB: check_state()
    CB-->>Loop: CLOSED ✅

    Loop->>PB: execute_as(uid=ops-writer, cmd)
    PB-->>Loop: {exit_code: 0, stdout: ""}

    Loop->>HM: run_post_hooks(result)
    HM->>Post: 01_audit_logger.py
    Post->>AL: write_jsonl(phase=execute, op_id, exit_code=0)
    Post-->>HM: exit 0 ✅

    HM->>Post: 02_circuit_check.py
    Post->>CB: record_success()
    CB-->>Post: CLOSED
    Post-->>HM: exit 0 ✅

    Loop->>LLM: tool_result → final response
    LLM-->>Loop: "已删除 /var/log/mysql/slow.log（47GB）"

    Loop->>AL: write_jsonl(phase=complete, bytes_freed=47GB)
    Loop->>Mem: maybe_save(event="slow.log confirmed delete")

    Loop-->>CLI: final_answer
    CLI-->>User: "已删除 slow.log（47GB），快照保留 24 小时"
```

---

## 图 3：权限管道状态机（PermissionManager）

```mermaid
flowchart TD
    INPUT["tool_call 到达\n{tool, command, risk_level}"]

    INPUT --> S0{"Step 0\nBashSecurityValidator"}
    S0 -->|严重命令\nrm-rf/ dd fork-bomb| DENY["❌ DENY\n记录 security_blocked\n+1 consecutive_denial"]
    S0 -->|通过| S1

    S1{"Step 1\ndeny_rules\n绝对黑名单"}
    S1 -->|命中黑名单| DENY
    S1 -->|未命中| S2

    S2{"Step 2\nmode_check"}
    S2 -->|plan 模式 + 非只读| DENY
    S2 -->|plan 模式 + 只读| ALLOW
    S2 -->|auto 模式 + risk≠HIGH| ALLOW
    S2 -->|default/auto + risk=HIGH| S3

    S3{"Step 3\nallow_rules\n白名单"}
    S3 -->|命中白名单| ALLOW["✅ ALLOW\n降权执行\n记录 audit"]
    S3 -->|未命中| S4

    S4{"Step 4\nask_user\nCLI 二次确认"}
    S4 -->|y 确认| ALLOW
    S4 -->|n 取消| DENY
    S4 -->|p 切换只读| MODE_CHANGE["⚙️ 切换 plan 模式\n重新评估"]

    ALLOW --> EXEC["执行\nPrivilegeBroker\nops-reader/writer"]
    DENY --> END["返回 security_blocked\n不执行"]
    MODE_CHANGE --> S2

    style DENY fill:#ff4444,color:#fff
    style ALLOW fill:#44aa44,color:#fff
    style EXEC fill:#2266cc,color:#fff
```

---

## 图 4：熔断器状态机（CircuitBreaker）

```mermaid
stateDiagram-v2
    [*] --> CLOSED: 初始化

    CLOSED --> CLOSED: record_success()\nfail_count = 0

    CLOSED --> HALF_OPEN: record_failure()\nfail_count ≥ 3\n写入 SQLite

    HALF_OPEN --> CLOSED: 探测请求成功\nrecord_success()\nfail_count = 0\n重置 SQLite

    HALF_OPEN --> OPEN: 探测请求失败\nrecord_failure()

    OPEN --> HALF_OPEN: 超过 reset_timeout\n（默认 60s）\n允许单次探测

    OPEN --> OPEN: 新请求到达\n直接拒绝\nhook exit 1

    note right of CLOSED
        正常执行状态
        所有 tool_call 放行
    end note

    note right of OPEN
        所有 tool_call 拒绝
        PostToolUse hook exit 1
        建议用户手动介入
    end note

    note right of HALF_OPEN
        半开探测状态
        仅允许一个请求通过
        等待结果决定转向
    end note
```

---

## 图 5：任务状态机（TaskManager — 6态 FSM）

```mermaid
stateDiagram-v2
    [*] --> pending: 创建任务

    pending --> in_progress: LLM 开始处理

    in_progress --> awaiting_approval: 需要用户确认\nHIGH/CRITICAL 风险

    awaiting_approval --> in_progress: 用户确认 y
    awaiting_approval --> security_blocked: 用户拒绝 n\n或超时

    in_progress --> completed: 工具执行成功\nexit_code = 0

    in_progress --> failed: 工具执行失败\nexit_code ≠ 0\n或 CircuitBreaker OPEN

    security_blocked --> [*]: 记录审计日志\n结束

    failed --> in_progress: ErrorRecovery\n重试（最多 3 次）
    failed --> [*]: 超过重试上限\n触发回滚

    completed --> [*]: 写审计日志\n更新记忆
```

---

## 图 6：Hook 管道执行流程（PreToolUse Detail）

```mermaid
flowchart LR
    CALL["LLM tool_call\nbash: rm /var/log/..."]

    CALL --> H1["Hook 01\n01_injection_check.py\n检测提示词注入"]
    H1 -->|exit 0| H2["Hook 02\n02_blacklist_check.py\n绝对黑名单"]
    H1 -->|exit 1| BLOCK["🚫 阻断\n工具调用取消"]

    H2 -->|exit 0| H3["Hook 03\n03_risk_validator.py\n危险参数校验"]
    H2 -->|exit 1| BLOCK

    H3 -->|exit 0| H4["Hook 04\n04_snapshot_hook.py\n执行前快照"]
    H3 -->|exit 1| BLOCK
    H3 -->|"exit 2\n注入风险警告"| H4

    H4 -->|exit 0| PM["PermissionManager\n.check()"]
    H4 -->|"exit 2\n注入快照路径"| PM

    PM -->|ALLOW| PB["PrivilegeBroker\n降权执行"]
    PM -->|DENY| BLOCK
    PM -->|ASK| CLI["CLI 确认框\n用户输入 y/n"]
    CLI -->|y| PB
    CLI -->|n| BLOCK

    PB --> RESULT["工具执行结果"]
    RESULT --> POST1["PostHook 01\naudit_logger"]
    POST1 --> POST2["PostHook 02\ncircuit_check"]
    POST2 --> LLM_CTX["注入 LLM 上下文"]

    BLOCK --> LOG["记录 security_blocked\n审计日志"]

    style BLOCK fill:#ff4444,color:#fff
    style PB fill:#2266cc,color:#fff
```

---

## 图 7：记忆系统架构（MemoryManager）

```mermaid
graph TD
    subgraph SOURCES["记忆来源"]
        EV1["Session 事件\n（操作成功/失败）"]
        EV2["用户指令\n（明确要求记忆）"]
        EV3["DreamConsolidator\n（7门检查后整合）"]
    end

    subgraph TYPES["四种记忆类型"]
        U["user 类型\nadmin_preferences.md\n管理员习惯"]
        P["project 类型\nknown_protected_paths.md\n受保护路径"]
        F["feedback 类型\nincident_history.md\n事件经验"]
        R["reference 类型\nreferences.md\n监控大屏/联系人"]
    end

    subgraph STORAGE[".memory/ 目录"]
        IDX["MEMORY.md\n索引文件\n（≤200行）"]
        FILES["各类型 .md 文件\n每条记忆独立文件"]
    end

    subgraph GATES["DreamConsolidator\n7门检查"]
        G4["Gate 4: 24h 冷却"]
        G6["Gate 6: ≥5 Session"]
        G7["Gate 7: PID 锁"]
    end

    subgraph LOAD["SessionStart 加载"]
        SL["加载 MEMORY.md 索引\n→ 注入 SystemPrompt"]
    end

    EV1 --> U & F
    EV2 --> U & P & R
    EV3 --> G4 --> G6 --> G7 --> FILES

    FILES --> IDX
    IDX --> SL

    style STORAGE fill:#f0f4ff
    style GATES fill:#fff4e0
```

---

## 图 8：项目目录结构树（Directory Tree）

```
ops-agent/
│
├── 📄 main.py                    # REPL 入口（asyncio.run）
├── 📄 config.py                  # AgentConfig + MODEL_PROFILES
├── 📄 CLAUDE.md                  # Claude Code 上下文文件
├── 📄 DESIGN.md                  # 架构设计文档 v2.0
├── 📄 requirements.txt
├── 📄 .env.example
├── 📄 .hooks.json                # Hook 配置
│
├── 📁 docs/
│   ├── PRD.md                   ← 本文件
│   ├── ARCHITECTURE.md          ← 架构图集（本文件）
│   ├── DOC-1-security-layer.md
│   ├── DOC-2-state-resilience.md
│   ├── DOC-3-rollback-audit.md
│   └── DOC-4-main-loop.md
|
│
├── 📁 core/                      # [s01,s06,s08-s14,s17,s18]
│   ├── agent_loop.py             # LoopState 主循环
│   ├── subagent.py               # 子 Agent 工厂
│   ├── context_manager.py        # 三策略压缩
│   ├── hook_manager.py           # Hook 执行器
│   ├── memory_manager.py         # 跨 Session 记忆
│   ├── system_prompt.py          # 动态 prompt 组装
│   ├── error_recovery.py         # 三策略恢复
│   ├── background.py             # OS 后台监控线程
│   ├── cron_scheduler.py         # Cron 调度器
│   ├── autonomous.py             # 自治巡检模式
│   ├── isolation.py              # op_id 绑定隔离
│   └── circuit_breaker.py        # 熔断器
│
├── 📁 hooks/                     # [s08] 外置安全脚本
│   ├── pre_tool/
│   │   ├── 01_injection_check.py
│   │   ├── 02_blacklist_check.py
│   │   ├── 03_risk_validator.py
│   │   └── 04_snapshot_hook.py
│   └── post_tool/
│       ├── 01_audit_logger.py
│       └── 02_circuit_check.py
│
├── 📁 perception/                # OS 感知层
│   ├── aggregator.py
│   ├── disk_monitor.py
│   ├── process_monitor.py
│   ├── network_monitor.py
│   └── log_monitor.py
│
├── 📁 security/                  # [s07] + OpsAgent 专属
│   ├── permission_manager.py     # 4步管道
│   ├── intent_classifier.py      # 规则+LLM 混合分类
│   ├── prompt_injection.py       # 注入检测
│   ├── privilege_broker.py       # 最小权限执行
│   └── rules/
│       └── intent_rules.yaml     # 可热更新规则
│
├── 📁 tools/                     # [s02,s19]
│   ├── registry.py
│   ├── mcp_client.py
│   ├── mcp_router.py
│   ├── plugin_loader.py
│   ├── read_tools.py
│   ├── write_tools.py
│   └── exec_tools.py
│
├── 📁 managers/                  # 状态管理
│   ├── state_store.py            # SQLite 后端
│   ├── task_manager.py           # 6态 FSM
│   └── audit_logger.py           # 8 phase JSONL
│
├── 📁 rollback/                  # 三级快照补偿
│   ├── snapshot.py
│   ├── compensations.py
│   └── recovery.py
│
├── 📁 teams/                     # [s15,s16]
│   ├── analyst.py
│   ├── executor.py
│   └── protocols.py
│
├── 📁 skills/                    # [s05] 运维手册
│   ├── disk-cleanup/SKILL.md
│   ├── process-management/SKILL.md
│   └── log-analysis/SKILL.md
│
├── 📁 .memory/                   # [s09] 运行时生成
├── 📁 .audit/                    # 审计日志，运行时生成
├── 📁 .snapshots/                # 快照，运行时生成
├── 📁 .claude/
│   └── scheduled_tasks.json      # durable cron 持久化
│
└── 📁 tests/
    ├── conftest.py
    ├── test_permission_manager.py
    ├── test_hook_manager.py
    ├── test_intent_classifier.py
    ├── test_circuit_breaker.py
    ├── test_snapshot.py
    ├── test_memory_manager.py
    ├── test_cron_scheduler.py
    ├── test_error_recovery.py
    ├── test_state_store.py
    ├── test_task_manager.py
    ├── test_privilege_broker.py
    ├── test_audit_logger.py
    ├── test_mcp_client.py
    └── test_agent_loop.py         # 集成测试（Mock LLM）
```
