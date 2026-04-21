# DOC-7：意图审查与分层权限策略设计方案

> 覆盖模块：`security/intent_classifier.py`（扩展）· `security/permission_manager.py`（扩展）· `security/policy_engine.py`（新增）  
> 核心目标：**审查员只做分类与解释，策略层根据运行模式决定处置，两层职责严格分离**
>
> | 版本 | 变更 |
> |------|------|
> | v1.0 | 初稿：结构化审查结果 + 两层架构 + 三模式策略表 |

---

## 一、问题背景

### 1.1 现有设计的缺陷

当前 `PermissionManager` 对非黑名单命令统一走 `ask_user`，存在以下问题：

**疲劳确认问题**：HIGH 和 MEDIUM 都弹确认框，用户在高频运维场景下会形成"连续点击通过"的习惯，确认框逐渐失去意义。

**风险提示 ≠ 风险控制**：只把危险说清楚，但最终决定仍可能被疲劳确认吞掉。

**三种模式行为分裂**：交互模式、自动模式、后台任务如果分别用不同逻辑，同一命令在不同场景下行为不同，测试和排障都变难。

**自动模式缺乏上限阈值**：用户放权给 LLM 时，必须提前定义超阈值怎么处理，不能临时"看情况放行"。

### 1.2 核心设计原则

> 审查员不关心场景，策略层不关心语义。

- 审查员：只做分类与解释，输出统一结构化结果
- 策略层：根据运行模式决定执行、询问、跳过、延后还是拒绝
- 两层之间通过结构化结果传递，不共享状态

---

## 二、审查员层设计

### 2.1 审查员的职责边界

审查员**不判断操作是否合理**（没有上下文，判断不了），只判断**命令的客观风险属性**：

- 这个命令是否具有毁灭性/不可逆性？
- 影响范围有多大？
- 是否需要人工介入？

### 2.2 结构化输出

```python
@dataclass
class CommandRiskResult:
    risk_level:       Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason:           str    # 人类可读的风险说明
    blast_radius:     str    # 影响范围描述，如"影响整个 /etc 目录"
    reversible:       bool   # 操作是否可逆（有快照可恢复也算可逆）
    needs_human:      bool   # 审查员建议是否需要人工确认（建议，非强制）
    suggested_action: str    # 建议处置，如"建议先备份再执行"
    classifier:       Literal["rule", "llm", "default"]  # 判定来源
```

**重要**：`needs_human` 是审查员的建议，不是强制要求。策略层可以根据运行模式忽略它。

### 2.3 三层判定流程

```
命令字符串
  → 第一层：绝对黑名单（硬编码，毫秒级）
      命中 → CRITICAL，reversible=False，直接返回
  → 第二层：规则引擎（正则匹配，毫秒级）
      命中白名单 → LOW，reversible=True
      命中危险模式 → HIGH/CRITICAL
  → 第三层：LLM 安全审查员（仅灰色地带，qwen3-8b，独立上下文）
      输出结构化 JSON → 解析为 CommandRiskResult
      超时/解析失败 → 默认 HIGH，needs_human=True（保守策略）
```

### 2.4 规则库设计

**白名单（直接 LOW）**：
```python
SAFE_PREFIXES = [
    "df", "du", "ls", "cat", "head", "tail", "grep", "find",
    "ps", "top", "free", "uptime", "who", "w", "last",
    "netstat", "ss", "lsof", "journalctl", "systemctl status",
    "stat", "file", "wc", "echo", "date", "hostname", "uname",
]
```

**危险模式（直接 HIGH/CRITICAL）**：
```python
DESTRUCTIVE_PATTERNS = [
    (r"rm\s+.*-[a-z]*r",              "CRITICAL", False),  # rm -r 系列
    (r"find\s+.+-exec\s+rm",          "CRITICAL", False),  # find + exec rm
    (r">\s*/dev/sd",                   "CRITICAL", False),  # 写磁盘设备
    (r"curl.+\|\s*(bash|sh)",          "CRITICAL", False),  # 远程代码执行
    (r"wget.+\|\s*(bash|sh)",          "CRITICAL", False),
    (r":\(\)\{.*:\|:&\}",             "CRITICAL", False),  # fork bomb
    (r"chmod\s+-R\s+[0-7]*7\s+/",    "HIGH",     True),   # 全局权限放开
    (r"systemctl\s+(stop|disable)",   "HIGH",     True),   # 停止服务
    (r"kill\s+-9",                     "HIGH",     True),   # 强制杀进程
]
```

**灰色地带（走 LLM 审查）**：
```
mv、cp、chmod（非全局）、tar + curl 组合、管道写操作、
数据库操作、crontab 修改...
```

---

## 三、策略层设计

### 3.1 三种运行模式

```python
@dataclass
class RunPolicy:
    mode:             str   # "interactive" / "auto" / "background"
    max_auto_risk:    str   # 自动执行的最高风险等级
    on_exceed:        str   # "ask" / "skip" / "deny"
    notify_on_skip:   bool  # 跳过时是否记录待办通知
    allow_irreversible: bool  # 是否允许不可逆操作
```

**三种模式的默认配置**：
```python
INTERACTIVE = RunPolicy(
    mode="interactive",
    max_auto_risk="LOW",
    on_exceed="ask",
    notify_on_skip=False,
    allow_irreversible=False,  # 不可逆操作需要显式二次确认
)

AUTO = RunPolicy(
    mode="auto",
    max_auto_risk="MEDIUM",    # 用户放权时设定，不能临时修改
    on_exceed="skip",          # 超阈值跳过，不临时询问（否则退回交互模式）
    notify_on_skip=True,
    allow_irreversible=False,
)

BACKGROUND = RunPolicy(
    mode="background",
    max_auto_risk="LOW",       # 后台任务默认更保守
    on_exceed="skip",
    notify_on_skip=True,       # 超阈值记录待办，用户下次上线处理
    allow_irreversible=False,
)
```

### 3.2 统一策略表

| 风险等级 | reversible | 交互模式 | 自动模式 | 后台模式 |
|---------|-----------|---------|---------|---------|
| LOW | 任意 | 直接执行 | 直接执行 | 直接执行 |
| MEDIUM | True | 显示说明后确认 | 若≤阈值则执行，否则跳过 | 仅执行白名单动作 |
| MEDIUM | False | 显示说明 + 二次确认 | 跳过并记录待办 | 跳过并记录待办 |
| HIGH | True | 默认阻断，显式二次确认 | 跳过并记录待办 | 跳过并记录待办 |
| HIGH | False | 默认阻断，显式二次确认 | 拒绝 | 拒绝 |
| CRITICAL | 任意 | 直接拒绝 | 直接拒绝 | 直接拒绝 |

**关键设计决策**：
- HIGH 在交互模式下"默认阻断"而不是"默认询问"，用户必须显式二次确认才能执行
- 自动模式超阈值一律跳过，不临时询问（否则等于退回交互模式）
- CRITICAL 在任何模式下都直接拒绝，不可绕过

### 3.3 分层确认 UI

不是所有非 LOW 都用同一个确认框：

**MEDIUM 确认**：
```
ℹ [MEDIUM] systemctl restart nginx
  影响：重启 nginx 服务，短暂中断流量（约 1-2s）
  可恢复：是
  确认执行? [y/n]
```

**HIGH 二次确认**：
```
⚠ [HIGH] rm /var/lib/mysql/slow.log
  影响：删除数据库慢查询日志，无法通过快照恢复
  可恢复：否
  建议：先备份再执行
  
  此操作需要二次确认，请输入 "yes" 继续：
```

**CRITICAL 拒绝**：
```
🚫 [CRITICAL] rm -rf /var/lib/mysql
  已拒绝：毁灭性操作，影响整个数据库目录
  审计记录已写入
```

---

## 四、后台任务的待办队列

### 4.1 设计原则

后台任务超阈值操作**不挂起执行流**，而是：
1. 记录到待办队列（SQLite `pending_actions` 表）
2. 生成通知
3. 用户下次上线时展示待处理列表

### 4.2 待办记录结构

```python
@dataclass
class PendingAction:
    id:           str
    task_id:      str          # 来自哪个后台任务
    cmd:          str          # 原始命令
    risk_result:  CommandRiskResult
    created_at:   float
    expires_at:   float        # 超时自动取消
    status:       Literal["pending", "approved", "rejected", "expired"]
```

### 4.3 后台任务分类

后台任务在注册时必须声明类型：

```python
class BackgroundTaskType(Enum):
    AUTO_EXECUTE = "auto_execute"   # 可自动执行（仅 LOW 风险操作）
    GENERATE_TODO = "generate_todo" # 只生成待办，不自动执行
```

这样避免执行链长期悬挂在半确认状态。

---

## 五、与现有模块的接口关系

```
用户输入
  → PromptInjectionDetector
  → LLM 推理
  → LLM 生成 tool_call
  → IntentClassifier.classify_command(cmd)
      → CommandRiskResult
  → PolicyEngine.evaluate(risk_result, current_mode)
      → PolicyDecision(action, message, requires_confirm)
  → PermissionManager（消费 PolicyDecision）
      → DENY / ASK / ALLOW
  → HookManager PreToolUse（黑名单兜底）
  → 工具执行
  → AuditLogger（记录完整链路，含 risk_result）
```

`PolicyEngine` 是新增模块，负责消费 `CommandRiskResult` 并根据当前 `RunPolicy` 输出处置决策。`PermissionManager` 不再自己判断风险，只执行 `PolicyEngine` 的决策。

---

## 六、实现优先级

**第一步（当前 Week 4）**：
- `IntentClassifier` 增加 `classify_command()` 方法，输出 `CommandRiskResult`
- `PermissionManager` 接入审查结果，实现交互模式的策略表
- 分层确认 UI（MEDIUM/HIGH/CRITICAL 三种样式）
- `SessionTrust` + `TempRule` 基础实现
- `grant_permission` / `revoke_permission` / `list_tokens` 工具
- `/permissions` REPL 命令

**第二步（Week 5 任务系统时）**：
- `TaskRecord` 与 `TempRule` 绑定（task_id → 令牌生命周期）
- LLM 令牌申请判断规则注入 system prompt

**第三步（Week 7 后台任务时）**：
- `PolicyEngine` 独立模块
- 自动模式和后台模式的策略实现
- 待办队列（`pending_actions` SQLite 表）
- `CronScheduler` 接入 `BackgroundTaskType`

---

## 七、令牌申请判断规则

### 7.1 令牌的本质

令牌是**批量授权**，不是单次授权。单次操作走正常确认流程即可，令牌的价值在于避免重复确认。

### 7.2 LLM 自主判断是否申请令牌

写入 system prompt 工具使用规范，LLM 自己决策：

```
申请令牌的条件（满足任一）：
  - 同类危险操作预计 ≥ 3 次
  - 预计执行时间 > 5 分钟
  - 涉及文件数量 > 10 个
  - 后台任务 / 无人值守场景
  - 已有 TaskRecord 且包含写操作

单次操作直接走确认流程，不申请令牌。
```

### 7.3 任务系统接入后的对应关系（Week 5+）

```
TaskRecord 创建
  → LLM 评估任务是否需要批量授权
  → 需要 → grant_permission(task_id=task.id, ...)
  → 用户审批 → TempRule 绑定 task_id
  → 任务执行期间命中令牌自动放行
  → TaskRecord 状态变 success/failed/cancelled
  → 令牌自动失效
```

一个任务对应一个令牌，令牌随任务生命周期自动管理，不需要手动撤销。

---

## 八、整体未来逻辑流程（含所有设计）

```
用户输入
  │
  ▼
①注入检测(1层)
  ▼
② LLM 推理（DeepSeek-R1，流式思维链可视化）
  │
  ├─ finish_reason=stop → 返回答案
  └─ finish_reason=tool_calls
       │
       ▼
③ 参数合法性检查
  JSON解析失败 / 空cmd → 终止性错误，不让LLM重试
  │
  ▼
④ SessionTrust 令牌检查
  命中活跃令牌
    → os.path.realpath 路径规范化
    → scope 前缀硬校验（防 ../ 绕过）
    → 通过 → 跳过⑤⑥，直接到⑦
  未命中 → 继续
  │
  ▼
⑤ IntentClassifier.classify_command()
  第一层：绝对黑名单 → CRITICAL，直接拒绝
  第二层：白名单规则 → LOW，直接放行到⑦
  第二层：危险模式正则 → HIGH/CRITICAL
  第三层：灰色地带 → LLM审查员(qwen3-8b，独立上下文)
  输出 CommandRiskResult {
    risk_level, reversible, blast_radius,
    needs_human, suggested_action
  }
  │
  ▼
⑥ PolicyEngine.evaluate(CommandRiskResult, RunPolicy)

  交互模式（default）：
    LOW              → allow
    MEDIUM+reversible → 分层确认框（显示风险说明）
    MEDIUM+!reversible → 二次确认框
    HIGH             → 默认阻断，显式输入"yes"才继续
    CRITICAL         → deny，不进确认流

  自动模式（auto）：
    ≤ max_auto_risk  → allow
    > max_auto_risk  → skip + 记录待办
    CRITICAL         → deny

  后台模式（background）：
    LOW              → allow
    其余             → skip + 记录待办队列（PendingAction）
    CRITICAL         → deny

  用户确认时可选授权范围
    → 生成 TempRule 加入 SessionTrust
    → 若绑定 task_id，随任务生命周期自动失效
  │
  ▼
⑦ HookManager PreToolUse（外置安全脚本）
  01_injection_check → 工具参数注入检测
  02_blacklist_check → 黑名单兜底（防绕过）
  03_risk_validator  → 风险参数校验
  04_snapshot_hook   → HIGH操作前自动快照
  exit 1 → 阻断
  exit 2 → 注入警告消息到 messages
  │
  ▼
⑧ PrivilegeBroker.execute()
  _resolve_privilege(risk_level) → ops-reader(9001) / ops-writer(9002)
  _write_script(cmd) → mkstemp + chmod 700（防TOCTOU）
  sudo -u ops-reader/ops-writer /bin/bash script.sh
  safe_env 白名单环境变量（防LD_PRELOAD注入）
  执行完立即删除脚本
  返回 ExecResult {stdout, stderr, exit_code, elapsed_ms}
  │
  ▼
⑨ PromptInjectionDetector.check_tool_output()
  工具输出间接注入检测（日志/文件内容里埋指令）
  INJECTED → 阻断，不注入LLM上下文
  │
  ▼
⑩ HookManager PostToolUse
  01_audit_logger → 8-phase JSONL 写入（append-only）
    {receive→perceive→reason→validate→snapshot→confirm→execute→complete}
    每条含 op_id / timestamp / risk_result / privilege / exit_code
  02_circuit_check → 连续失败熔断
    CLOSED → HALF_OPEN → OPEN
  │
  ▼
  ToolResult → 注入 messages → 继续 LLM 推理循环
```

### 横切关注点

**连续拒绝熔断**：
```
consecutive_denials 计数
deny/ask → +1；成功执行 → 清零
≥ 3 → 强制终止循环，返回提示用户
```

**运行模式切换**：
```
/mode default   → INTERACTIVE policy
/mode plan      → INTERACTIVE + 所有写操作 deny
/mode auto      → AUTO policy（用户预设 max_auto_risk）
后台任务启动    → BACKGROUND policy（自动切换，任务结束恢复）
```

**令牌生命周期**：
```
grant_permission() → 用户确认 → TempRule 加入 SessionTrust
失效方式（三选一）：
  TTL 过期
  task_id 对应 TaskRecord 状态变终态
  revoke_permission() 主动撤销

令牌状态注入 system-reminder，LLM 每轮可见
```

**快照与回滚**（Week 6）：
```
HIGH 操作前 → 04_snapshot_hook 自动快照
执行失败 → rollback/recovery.py 三级补偿
  L1 文件级：从 .snapshots/ 恢复
  L2 配置级：恢复原始权限位
  L3 服务级：systemctl start 恢复服务
```

### 实现状态总览

| 环节 | 状态 | 计划周次 |
|------|------|---------|
| ① 注入检测 | ✅ 已实现 | — |
| ② LLM 流式推理 | ✅ 已实现 | — |
| ③ 参数合法性 | ✅ 已实现 | — |
| ④ 令牌检查 | ❌ 设计完成 | Week 4 |
| ⑤ classify_command | ❌ 设计完成 | Week 4 |
| ⑥ PolicyEngine | ❌ 设计完成 | Week 4/7 |
| ⑦ PreToolUse hooks | ⚠️ 骨架，全是桩 | Week 4 |
| ⑧ PrivilegeBroker | ❌ 设计完成 | Week 4 |
| ⑨ 输出注入检测 | ✅ 已实现 | — |
| ⑩ PostToolUse + 审计 | ⚠️ 骨架，全是桩 | Week 4 |
| 熔断 | ✅ 已实现 | — |
| 任务系统令牌绑定 | ❌ 待设计 | Week 5 |
| 快照回滚 | ❌ 待实现 | Week 6 |
| 自动巡检模式 | ❌ 待实现 | Week 7 |

---

## 九、遗留问题

1. LLM 审查员 prompt 设计：如何让 qwen3-8b 稳定输出结构化 JSON，避免解析失败
2. `blast_radius` 字段标准化：如何量化影响范围，避免描述过于主观
3. 待办队列过期策略：超时未处理的待办如何处理（自动拒绝 vs 永久保留）
4. 自动模式阈值修改权限：谁可以修改 `max_auto_risk`，是否需要审计记录
5. 令牌申请的 dry-run 预览：`grant_permission` 确认前执行 find 预览影响范围（Week 4 第二步）
6. `ask_user` 语义修复：现在 `behavior==ask` 直接返回错误给 LLM，应改为暂停推理等待用户终端输入
7. 隐式令牌匹配的意图漂移问题：令牌按命令模式匹配，无法区分"合法使用"和"借用令牌做其他事"。
   缓解方案：`task_id` 绑定防止跨任务借用；同任务内的注入依赖注入检测（环节①⑦）兜底；
   审计日志记录每次令牌使用的命令和当前任务，事后可追溯。
   根本解法需等任务系统完善后，通过任务状态机约束令牌有效范围。


