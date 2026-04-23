# DOC-8：任务权限系统设计方案

> 覆盖模块：`managers/task_manager.py` · `security/session_trust.py` · `security/permission_manager.py`（扩展）  
> 核心目标：**任务、令牌、命令三层分离；令牌生命周期硬绑定任务状态机；JIT 渐进式授权**
>
> | 版本 | 变更 |
> |------|------|
> | v1.0 | 初稿：三层执行模型 + 权限模板 + JIT 申请 + 生命周期硬绑定 |
> | v1.1 | 补充：任务依赖管理 + 验证推动机制 + activeForm 字段（参考 Claude Code 任务系统设计）|

---

## 一、三层执行模型

### 1.1 层级定义

| 层级 | 本质 | 存储位置 | 解决的问题 |
|------|------|---------|-----------|
| 长期规划 (Plan) | 战略蓝图，跨会话目标 | `plans` 表 + 向量记忆 | 终点在哪？目标不因对话结束而消失 |
| 系统任务 (Task) | 执行容器，有生命周期 | `tasks` 表 + `LoopState.active_task_id` | 谁在干活？关联审计、权限令牌、资源归属 |
| 会话内规划 (CoT) | LLM 推理链，临时策略 | `messages` 上下文 | 下一步干什么？处理工具报错和逻辑分支 |

**关键区别**：
- 会话内规划是**脆弱的**——上下文窗口满了 LLM 会忘记
- 系统任务是**刚性的**——数据库里的状态不会因 LLM 遗忘而改变
- 长期规划是**持久的**——即使进程重启，目标依然存在

### 1.2 任务 ID 命名规范

采用 `Type:Name:UUID` 格式，确保唯一且可追溯：

```
interactive:8b3d...          ← 用户交互任务（隐式，自动生成）
task:cleanup_tmp:f2a1...     ← 显式任务（用户明确指定目标）
cron:disk_check:c9e0...      ← 定时任务
cruise:nginx_monitor:a1b2... ← 自动巡航任务
```

### 1.3 任务状态机（6态）

```
pending → in_progress → awaiting_approval → completed
                     ↘ security_blocked
                     ↘ failed → (retry) → pending
                     ↘ suspended          ← 等待人工确认（后台任务）
```

**状态说明**：
- `awaiting_approval`：JIT 权限申请等待用户审批，任务挂起
- `security_blocked`：触碰安全边界，需要人工介入
- `suspended`：后台任务无人值守时挂起，保存完整上下文

---

## 二、令牌（TempRule）设计

### 2.1 令牌的本质

令牌是**批量授权**，不是单次授权。单次操作走正常确认流程，令牌的价值在于：
- 避免重复确认（批量操作）
- 无人值守时的预授权（后台/巡航任务）

### 2.2 令牌数据结构

```python
@dataclass
class TempRule:
    rule_id:      str                    # 唯一 ID
    task_id:      str                    # 绑定的 TaskRecord ID（硬约束）
    scope_type:   Literal["path", "behavior", "command_pattern"]
    scope_value:  str                    # 路径前缀 / 行为类型 / 正则模式
    risk_ceiling: str                    # 允许的最高风险等级
    created_at:   float
    expires_at:   float                  # TTL 过期时间
    status:       Literal["active", "invalid", "expired"]
    escalation_history: list[dict]       # 追加授权记录（JIT 历史）
```

**scope_type 说明**：
- `path`：路径前缀匹配，如 `/tmp/`，操作路径必须全部在此前缀下
- `behavior`：行为类型，如 `read_only`，允许任意路径但只允许只读操作
- `command_pattern`：正则匹配，如 `rm /tmp/.*\.log`

### 2.3 路径匹配安全规则

令牌命中时必须做路径规范化，防止绕过：

```python
def match_token(cmd: str, rule: TempRule) -> bool:
    if rule.scope_type == "path":
        # 解析命令中所有路径参数，逐个验证
        paths = extract_all_paths(cmd)
        normalized = [os.path.realpath(p) for p in paths]
        # 所有路径必须都在 scope 前缀下，一个越界就拒绝
        return all(p.startswith(rule.scope_value) for p in normalized)
    elif rule.scope_type == "behavior":
        return classify_cmd_behavior(cmd) == rule.scope_value
    elif rule.scope_type == "command_pattern":
        return bool(re.fullmatch(rule.scope_value, cmd.strip()))
```

**注意**：`rm /tmp/a.log /etc/passwd` 包含越界路径，即使匹配了 `/tmp/` 前缀也必须拒绝。

---

## 三、任务数据模型

### 3.1 TaskRecord 字段

```python
@dataclass
class TaskRecord:
    id:          str       # Type:Name:UUID 格式
    title:       str       # 祈使句标题，如 "清理 /var/log 过期日志"
    active_form: str       # 进行时形式，用于 UI spinner，如 "正在清理 /var/log 过期日志"
    status:      str       # 见状态机
    risk_level:  str
    created_at:  float
    updated_at:  float
    expires_at:  float | None   # suspended 状态的超时时间
    blocks:      list[str]      # 此任务阻塞哪些任务 ID
    blocked_by:  list[str]      # 哪些任务 ID 阻塞此任务
```

**`active_form` 的价值**：UI spinner 显示"正在清理 /var/log 过期日志"而非"exec_bash"，用户在等待时看到的是"正在做什么"而非工具名。

### 3.2 SQLite Schema

```sql
CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    active_form TEXT,                    -- 进行时描述，用于 spinner
    status      TEXT NOT NULL,
    task_type   TEXT,                    -- interactive/cron/cruise
    risk_level  TEXT,
    blocks      TEXT DEFAULT '[]',       -- JSON 数组：此任务阻塞哪些任务 ID
    blocked_by  TEXT DEFAULT '[]',       -- JSON 数组：哪些任务 ID 阻塞此任务
    created_at  REAL,
    updated_at  REAL,
    expires_at  REAL
);

CREATE TABLE temp_rules (
    rule_id         TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    scope_type      TEXT,
    scope_value     TEXT,
    risk_ceiling    TEXT,
    created_at      REAL,
    expires_at      REAL,
    status          TEXT DEFAULT 'active',
    escalation_log  TEXT        -- JSON，追加授权历史
);

CREATE TABLE pending_actions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT REFERENCES tasks(id),
    cmd         TEXT,
    risk_result TEXT,
    created_at  REAL,
    expires_at  REAL,
    status      TEXT DEFAULT 'pending'
);
```

**SQLite 注意**：每次连接必须执行 `PRAGMA foreign_keys = ON`，否则外键级联不生效。

### 3.3 任务依赖管理

任务间依赖通过双向链表式的 `blocks`/`blocked_by` 字段实现：

```python
class TaskManager:
    def block_task(self, from_id: str, to_id: str) -> None:
        """from_id 完成前，to_id 不能开始。同时维护两端。"""
        from_task = self.get(from_id)
        to_task = self.get(to_id)
        if to_id not in from_task.blocks:
            self._update(from_id, blocks=[*from_task.blocks, to_id])
        if from_id not in to_task.blocked_by:
            self._update(to_id, blocked_by=[*to_task.blocked_by, from_id])

    def transition(self, task_id: str, new_status: str) -> None:
        self._db.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (new_status, time.time(), task_id)
        )
        if new_status in ("completed", "failed", "cancelled"):
            # 1. 级联失效所有关联令牌
            self._db.execute(
                "UPDATE temp_rules SET status='invalid' WHERE task_id=?",
                (task_id,)
            )
            # 2. 清理其他任务对此任务的依赖引用
            self._cleanup_dependency_refs(task_id)

    def _cleanup_dependency_refs(self, task_id: str) -> None:
        """任务终态时，从所有其他任务的 blocks/blocked_by 中移除对它的引用。"""
        for task in self.list_all():
            changed = False
            new_blocks = [t for t in task.blocks if t != task_id]
            new_blocked_by = [t for t in task.blocked_by if t != task_id]
            if new_blocks != task.blocks or new_blocked_by != task.blocked_by:
                self._update(task.id, blocks=new_blocks, blocked_by=new_blocked_by)
```

**注意**：删除或终止任务时必须清理依赖引用，否则其他任务会永久卡在 `blocked_by` 状态。

---

## 四、验证推动机制（Verification Nudge）

### 4.1 问题

LLM 执行完一系列写操作后，倾向于直接宣布"任务完成"，而不主动验证操作效果。

### 4.2 设计

在 `_phase4_execute` 完成后检查：如果当前任务连续执行了 3+ 个写操作（`cmd_type` 为 `file` 或 `service`）且没有任何读操作验证，在 tool_result 里追加提示：

```python
def _maybe_nudge_verification(self, state: LoopState, result: ToolResult) -> ToolResult:
    """连续写操作后，推动 LLM 主动验证效果。"""
    write_count = sum(
        1 for e in state.tool_executions[-5:]
        if e.cmd_type in ("file", "service")
    )
    has_recent_read = any(
        e.cmd_type == "read" for e in state.tool_executions[-3:]
    )
    if write_count >= 3 and not has_recent_read:
        nudge = "\n\n[系统提示] 已连续执行多个写操作，建议验证操作效果（如检查服务状态、确认文件内容）后再继续。"
        return ToolResult(
            **{**result.__dict__, "output": result.output + nudge}
        )
    return result
```

这是**结构性推动而非硬约束**——LLM 可以忽略，但大多数情况下会触发验证行为。

---

### 3.1 核心原则

令牌不是独立实体，是 TaskRecord 的从属子节点。任务状态变为终态时，所有关联令牌**自动、强制**失效，不依赖应用层主动撤销。

### 3.2 数据库 Schema

```sql
CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    status      TEXT,           -- pending/in_progress/awaiting_approval/completed/failed/suspended
    task_type   TEXT,           -- interactive/cron/cruise
    risk_level  TEXT,
    created_at  REAL,
    updated_at  REAL,
    expires_at  REAL            -- suspended 状态的超时时间
);

CREATE TABLE temp_rules (
    rule_id         TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    scope_type      TEXT,
    scope_value     TEXT,
    risk_ceiling    TEXT,
    created_at      REAL,
    expires_at      REAL,
    status          TEXT DEFAULT 'active',
    escalation_log  TEXT        -- JSON，追加授权历史
);

-- 待办队列（后台任务超阈值操作）
CREATE TABLE pending_actions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT REFERENCES tasks(id),
    cmd         TEXT,
    risk_result TEXT,           -- JSON
    created_at  REAL,
    expires_at  REAL,
    status      TEXT DEFAULT 'pending'  -- pending/approved/rejected/expired
);
```

**关键**：`REFERENCES tasks(id) ON DELETE CASCADE` 确保任务删除时令牌级联失效。

**SQLite 注意事项**：每次连接必须执行 `PRAGMA foreign_keys = ON`，否则外键约束不生效。

### 3.3 任务终态触发令牌失效

```python
class TaskManager:
    TERMINAL_STATES = {"completed", "failed", "cancelled"}

    def transition(self, task_id: str, new_status: str) -> None:
        self._db.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (new_status, time.time(), task_id)
        )
        if new_status in self.TERMINAL_STATES:
            # 级联失效所有关联令牌
            self._db.execute(
                "UPDATE temp_rules SET status='invalid' WHERE task_id=?",
                (task_id,)
            )
```

---

## 五、权限模板（Permission Template）

### 4.1 用途

长期任务（Job/巡航）在启动前无法预测每条命令，但可以定义**安全边界（Sandbox）**。Job 模板绑定预设授权集，实例化为 Task 时自动签发基础通行证。

### 4.2 模板结构

```python
@dataclass
class PermissionTemplate:
    template_id:    str
    name:           str
    # 只读边界：允许对全系统执行只读操作，但排除敏感路径
    read_allowed:   bool = True
    read_excludes:  list[str] = field(default_factory=lambda: [
        "/etc/shadow", "/etc/gshadow", "/proc/*/mem", "/root/.ssh"
    ])
    # 写边界：允许操作的路径前缀列表
    write_paths:    list[str] = field(default_factory=list)
    # 服务边界：允许操作的服务名白名单
    service_names:  list[str] = field(default_factory=list)
    # 风险上限
    max_risk:       str = "MEDIUM"
```

**示例**：磁盘清理巡航任务的模板

```python
DISK_CLEANUP_TEMPLATE = PermissionTemplate(
    template_id="disk_cleanup_v1",
    name="磁盘清理",
    read_allowed=True,
    write_paths=["/tmp/", "/var/log/", "/var/cache/"],
    service_names=[],
    max_risk="MEDIUM",
)
```

### 4.3 令牌签发时机

```
Job 定义（含 PermissionTemplate）
  → 调度器触发，实例化为 Task
  → TaskManager 根据模板自动签发 TempRule（基础通行证）
  → 令牌加载进 SessionTrust
  → Task 开始执行
```

**注意**：令牌绑定的是 **Task 实例**，不是 Job 模板。每次 Job 触发新 Task 时重新签发，不复用旧令牌。

---

## 六、JIT 渐进式授权

### 5.1 触发流程

```
命令越界（令牌不匹配）
  → PermissionManager 拦截
  → 判断是否可申请（CRITICAL 直接拒绝，不允许 JIT）
  → 构造申请包
  → 用户审批 / 自动提权规则
  → 追加 TempRule 到当前 task_id（不废弃旧令牌）
  → 记录追加历史到 escalation_log
  → 任务从断点恢复
```

### 5.2 申请包结构

```python
@dataclass
class PermissionEscalationRequest:
    task_id:         str
    original_scope:  str           # 原有令牌范围
    requested_scope: str           # 申请扩展的范围
    reason:          str           # LLM 生成的变更说明
    suggested_risk:  str           # 申请的风险等级
    context_cmd:     str           # 触发申请的具体命令
```

### 5.3 申请频率熔断

防止 LLM 连续发起权限申请导致用户疲劳：

```
同一 task_id 在 10 分钟内发起 JIT 申请 ≥ 3 次
  → 暂停任务
  → 提示用户："该任务需要的权限超出预期，建议重新定义任务范围"
  → 任务状态变为 security_blocked
```

### 5.4 自动提权规则（Auto-Escalation）

后台/巡航任务无人值守时，满足以下条件可自动追加令牌：

```python
AUTO_ESCALATION_RULES = [
    # 修改日志级别，且在维护时间窗口内
    {
        "condition": lambda req: "log" in req.requested_scope and is_maintenance_window(),
        "max_risk": "MEDIUM",
    },
    # 清理临时文件，路径在白名单内
    {
        "condition": lambda req: req.requested_scope.startswith("/tmp/"),
        "max_risk": "LOW",
    },
]
```

---

## 七、suspended 状态处理

### 6.1 挂起条件

后台任务遇到需要人工确认的操作时：
1. 将当前 `messages` 和 `LoopState` 序列化存入数据库
2. 任务状态变为 `suspended`
3. 设置 `expires_at`（默认 72 小时）
4. 发送通知（钉钉/邮件/终端提示）
5. `active_task_id` 置空，AgentLoop 处理下一个排队任务

### 6.2 超时策略

```
expires_at 到期
  → status 变为 failed
  → 关联令牌全部失效
  → 审计日志记录"超时未处理"
  → 通知用户
```

超时时间可在 Job 模板中配置，默认 72 小时。

### 6.3 恢复流程

```
用户上线，看到 suspended 任务列表
  → 选择"继续执行"
  → 从数据库加载 messages + LoopState
  → 恢复 active_task_id
  → 从断点继续 LLM 推理
```

---

## 八、与现有模块的接口关系

```
PermissionManager.check(tool_name, tool_args)
  → 先查 SessionTrust（令牌检查，0.1ms）
      命中 → 路径规范化 + scope 校验 → 通过则跳过后续
      未命中 → 继续
  → IntentClassifier.classify_command()
  → PolicyEngine.evaluate()
      ask/deny → JIT 申请 or 直接拒绝
      allow → 执行

TaskManager.transition(task_id, new_status)
  → 终态 → 级联失效 temp_rules
  → suspended → 序列化上下文 + 设置 expires_at

SessionTrust
  → 内存字典：task_id → List[TempRule]
  → 启动时从数据库加载 active 令牌
  → 任务终态时同步清除内存缓存
```

---

## 九、实现优先级

| 功能 | 优先级 | 计划周次 |
|------|--------|---------|
| TaskRecord + 6态状态机 + `active_form` 字段 | 高 | Week 5 |
| `blocks`/`blocked_by` 依赖字段（先留字段，不实现解析） | 高 | Week 5 |
| TempRule 表 + 生命周期硬绑定 + 依赖引用清理 | 高 | Week 5 |
| SessionTrust 令牌检查接入 PermissionManager | 高 | Week 5 |
| grant_permission / revoke_permission 工具 | 高 | Week 5 |
| 验证推动机制（verification nudge） | 中 | Week 5 |
| JIT 申请流程 + 申请频率熔断 | 中 | Week 5-6 |
| 依赖解析（blocked_by 阻塞检查） | 中 | Week 6 |
| 权限模板（PermissionTemplate） | 中 | Week 7 |
| suspended 挂起/恢复 | 中 | Week 7 |
| 自动提权规则（Auto-Escalation） | 低 | Week 7+ |
| 长期规划（Plan 表） | 低 | Week 8+ |

---

## 十、遗留问题

1. `suspended` 任务的 messages 序列化格式：JSON 还是 MessagePack？大型上下文的存储效率
2. 多任务并发时 SessionTrust 的线程安全：内存字典需要加锁还是用 asyncio.Lock
3. 自动提权规则的配置方式：硬编码 vs YAML 配置文件
4. JIT 申请的 dry-run 预览：申请前执行 `find` 预览影响范围，避免用户盲目审批
5. 令牌的审计可见性：每次令牌命中都要写审计日志，高频只读操作会产生大量日志噪音
