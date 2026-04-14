# OpsAgent v2.0 — 产品需求文档（PRD）

> **版本**：v2.0  
> **日期**：2026-04-13  
> **状态**：In Review  
> **负责人**：Platform Engineering Team

---

## 一、产品概述（Executive Summary）

### 1.1 产品定位

OpsAgent 是面向 **Linux 企业运维工程师** 的安全增强型 AI 自动化助手。它以 LLM（DeepSeek-R1 / Qwen3）为推理核心，以 Harness 为执行容器，解决以下核心问题：

> **"AI 能否在运维场景中安全地执行具有破坏性潜力的系统命令？"**

答案是：**可以——但前提是每个命令必须通过可审计、可回滚、最小权限的安全管道。**

### 1.2 核心价值主张

| 维度 | 传统运维脚本 | 裸 LLM API | **OpsAgent** |
|------|------------|-----------|-------------|
| 智能适应性 | ❌ 规则固化 | ✅ 灵活推理 | ✅ |
| 安全可控性 | ✅ 逻辑固定 | ❌ 无管控 | ✅ Hook 管道 + 权限门 |
| 操作可追溯 | ⚠ 依赖日志 | ❌ | ✅ JSONL 审计 + op_id |
| 失败可恢复 | ⚠ 手动 | ❌ | ✅ 三级快照补偿 |
| 上下文记忆 | ❌ | ❌ 单 Session | ✅ 跨 Session 记忆 |

---

## 二、目标用户与场景

### 2.1 主要用户

**运维工程师（SRE / DevOps）**
- 需要在多台 Linux 服务器上执行巡检、清理、诊断操作
- 对 AI 自主执行高风险命令有合理顾虑
- 需要完整的操作审计记录用于合规

### 2.2 核心使用场景

#### 场景 A：主动运维（Proactive Ops）
```
触发：定时 Cron（每 30 分钟） 
任务：磁盘使用率巡检 → /var/log 超 85% 触发清理分析
输出：清理建议 + 等待确认 / 自动执行（auto 模式）
```

#### 场景 B：响应式诊断（Reactive Diagnosis）
```
触发：用户指令："系统响应变慢，帮我排查"
流程：perception → ps / top / netstat → LLM 分析 → 给出根因报告
输出：结构化诊断报告 + 建议操作（需确认方执行）
```

#### 场景 C：计划性清理（Planned Cleanup）
```
触发：用户指令："清理 3 个月前的日志"
流程：快照 → 执行 → 审计
安全：执行前自动快照，24 小时内可回滚
```

### 2.3 非目标场景

- ❌ 生产数据库 DDL 操作（超出安全边界）
- ❌ 跨网络多节点并行部署（当前版本仅单节点）
- ❌ 替代完整的 ITSM 工单系统

---

## 三、功能需求


### 3.0 基本需求
1、OS 环境深度感知：Agent 能够自动调用底层工具(如 lsof, netstat, journalctl)获取进程、网络、日志等实时上下文。

2、MCP 运维插件化：采用插件化架构(参考 MCP 协议)，将常用运维动作封装为 Agent 可调用的 Tools。

3、安全意图校验器：建立风险识别模型或规则库，对 LLM 生成的原始指令进行“二次过滤”，识别高危参数(如 rm 等参数、不安全的 chmod 等)。

4、最小权限代理执行：实现 Agent 的权限隔离，核心运维动作需在受限的Account下运行，非必要不使用 root。

5、推理链路溯源：完整记录“接收指令 -> 感知环境 -> 推理决策 -> 安全校验 -> 执行结果”的闭环日志，支持异常回溯。

### 非功能需求
	
确定性与可靠性：严禁 Agent 在未授权情况下修改系统关键配置文件

抗注入能力：Agent需能识别提示词注入(Prompt Inject)，防止攻击者通过对话诱导 Agent 执行恶意代码

### 3.1 P0（Must Have — MVP 必须交付）

#### F-01：安全感知主循环
- `core/agent_loop.py`：基于 `LoopState` 的异步主循环
- 支持 `turn_count` 上限（默认 50 turns/session）
- 每轮显式记录 `transition_reason`（状态转换原因）
- 引用：s01 Agent Loop 架构哲学

#### F-02：4 步权限管道
- `security/permission_manager.py`
- Step 1: deny_rules（绝对黑名单，不可绕过）
- Step 2: mode_check（plan 模式锁只读）
- Step 3: allow_rules（白名单自动放行）
- Step 4: ask_user（CLI 二次确认）
- 三种运行模式：`default` / `plan` / `auto`
- **验收标准**：`rm -rf /` 在任意模式下均被 deny，永不到达 ask_user

#### F-03：Hook 外置安全管道
- `core/hook_manager.py` + `hooks/` 目录
- PreToolUse hooks：injection_check → blacklist → risk_validator → snapshot
- PostToolUse hooks：audit_logger → circuit_check
- Hook 退出码合约：`0`=继续，`1`=阻断，`2`=注入上下文消息
- **验收标准**：hook 脚本崩溃不影响主循环（fail-safe）

#### F-04：意图分类器
- `security/intent_classifier.py`
- 规则引擎（YAML 配置，热更新）优先匹配
- 规则未命中 → UNKNOWN → 轻量 LLM 安全审查员二次分类
- 输出：`{intent, risk_level: LOW/MEDIUM/HIGH/CRITICAL, matched_rule}`
- **验收标准**：`rm -rf /` 分类为 CRITICAL，"查看磁盘" 分类为 LOW

#### F-05：OS 感知层
- `perception/aggregator.py`：统一入口，返回结构化 `PerceptionSnapshot`
- 覆盖：disk（df/du/lsof）、process（ps/top）、network（netstat/ss）
- **验收标准**：单次感知耗时 < 2s（含 timeout 保护）

#### F-06：JSONL 审计日志
- `managers/audit_logger.py`：8 phase 全程记录
- phases: `intent` → `permission` → `snapshot` → `execute` → `result` → `circuit` → `memory` → `complete`
- 每条记录含 `op_id`, `timestamp`, `user`, `command`, `exit_code`
- **验收标准**：每次工具调用均有对应审计记录，不可篡改（append-only）

### 3.2 P1（Should Have — v2.0 核心功能）

#### F-07：三级快照补偿
- L1：文件快照（< 100MB 全量 / 否则仅元数据 + diff）
- L2：配置快照（`/etc/` 关键配置）
- L3：服务状态快照（systemctl 状态 + 关键进程 PID）
- 保留策略：默认 24 小时自动清理
- **验收标准**：L1 快照后执行 `rm` + 回滚，文件完整恢复

#### F-08：跨 Session 记忆
- `core/memory_manager.py`
- 四种类型：user / project / feedback / reference
- 运维场景映射：管理员偏好 / 受保护路径 / 事件经验 / 监控大屏
- DreamConsolidator：7门检查防止记忆库无限增长（24h 冷却，5 Session 阈值）
- **验收标准**：重启后仍能加载 "已知 /data/mysql/ 为受保护路径"

#### F-09：熔断器
- `core/circuit_breaker.py`
- 状态机：`CLOSED → HALF_OPEN → OPEN`
- 连续失败 ≥ 3 次 → OPEN（拒绝新操作）
- HALF_OPEN 单次探测成功 → CLOSED
- 状态持久化到 SQLite（重启后恢复）

#### F-10：错误恢复三策略
- Strategy 1：`stop_reason == max_tokens` → 注入 CONTINUATION_MESSAGE 续写（最多 3 次）
- Strategy 2：API 报 `overlong_prompt` → 触发 auto_compact（LLM 摘要压缩历史）
- Strategy 3：`ConnectionError / RateLimitError` → 指数退避（base=1s，最大 30s，3 次重试）

#### F-11：Cron 调度器
- `core/cron_scheduler.py`：标准 5 字段 cron 表达式
- session-only（内存，进程退出消失）和 durable（持久化到 `.claude/scheduled_tasks.json`）
- 错过任务检测：重启后显示错过的计划任务，用户决定是否补执行
- CronLock：PID 文件锁，防止多进程重复触发

### 3.3 P2（Nice to Have — 后续迭代）

#### F-12：多角色团队协作
- Analyst（只读上下文）/ Executor（受限上下文）/ Auditor
- MessageBus 协作 + 安全审批握手

#### F-13：MCP 插件接入
- `tools/mcp_client.py`：stdio MCPClient
- `tools/mcp_router.py`：命名约定 `mcp__{server}__{tool}`
- 所有 MCP 工具走同一权限门（不形成安全旁路）

#### F-14：自治巡检模式
- `core/autonomous.py`：idle_poll 模式，无用户输入时主动巡检
- 防止上下文压缩后角色漂移的身份重注入机制

---

## 四、非功能需求

### 4.1 性能要求

| 指标 | 目标值 | 说明 |
|------|--------|------|
| OS 感知延迟 | < 2s | 单次 `PerceptionSnapshot` 采集 |
| Hook 执行延迟 | < 500ms/个 | 单个 PreToolUse/PostToolUse hook |
| 权限决策延迟 | < 100ms | 纯规则匹配路径（无 LLM） |
| 意图分类延迟（规则命中） | < 50ms | YAML 规则引擎匹配 |
| 意图分类延迟（LLM 兜底） | < 3s | 轻量模型（qwen3-8b）二次分类 |
| 快照创建 | < 5s | < 100MB 文件，L1 快照 |
| SQLite 写入 | < 10ms | 审计日志单条追加 |

### 4.2 可靠性要求

- Hook 脚本崩溃必须 fail-safe（不阻断主循环，记录错误继续）
- 熔断器状态持久化：进程重启后恢复 OPEN 状态，不自动重置
- 审计日志：append-only，文件权限 `644`，不可被普通进程覆盖
- `asyncio.TaskGroup` 替代 `asyncio.gather()`，防止孤儿 Task 积累

### 4.3 安全要求

- **绝对黑名单**（硬编码，不可通过配置覆盖）：
  - `rm -rf /`、`dd if=/dev/zero of=/dev/sda`、`chmod -R 777 /`
  - `:(){ :|:& };:` fork bomb 模式
- **最小权限执行**：
  - `ops-reader`（uid=9001）：只读命令（df/ps/netstat/journalctl）
  - `ops-writer`（uid=9002）：写操作（rm/mv/cp/chmod），非 root
- **提示词注入防护**：所有工具输出在注入 LLM 上下文前通过 `prompt_injection.py` 扫描

### 4.4 可观测性要求

- 每次工具调用：8 phase 审计记录
- 每次 Session：`session_<id>.jsonl` 完整记录
- 关键指标通过 `managers/audit_logger.py` 统计：
  - `total_ops`、`blocked_ops`、`circuit_breaker_trips`、`rollback_count`

---

## 五、架构约束

1. **Python 3.11+**（`asyncio.TaskGroup` 支持）
2. **依赖最小化**：核心 Harness 只依赖 `openai`、`pydantic`、`pyyaml`、`aiosqlite`
3. **模型无关性**：通过 `config.py MODEL_PROFILES` 切换模型，Agent 核心代码不含模型特定逻辑
4. **DeepSeek-Reasoner 限制**：`deepseek-reasoner` 不支持 function calling，需路由到 `deepseek-chat`（在 `MODEL_PROFILES` 中标注）
5. **单节点优先**：当前版本不考虑分布式部署，SQLite 替代分布式数据库
6. 采用B/S架构开发，软件需部署在自主指令系统LoongArch架构+麒麟高级服务器版V11上运行;

7. 其余实现条件无硬性要求，建议优先使用国产软件;

8. 大模型选型鼓励使用国产开源模型(如 DeepSeek, Qwen3)或通过微调后的模型
---

## 六、依赖与集成

### 6.1 外部依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| openai | ≥1.30 | LLM API 调用（DeepSeek/Qwen3 兼容） |
| pydantic | ≥2.7 | 数据模型验证 |
| pyyaml | ≥6.0 | 意图规则 YAML 加载 |
| aiosqlite | ≥0.20 | 异步 SQLite（熔断器/任务状态持久化） |
| croniter | ≥2.0 | Cron 表达式解析 |
| python-dotenv | ≥1.0 | 环境变量加载 |

### 6.2 系统依赖

- Linux（Ubuntu 22.04+ / RHEL 8+）
- 系统命令：`df`、`du`、`lsof`、`ps`、`netstat`、`ss`、`journalctl`
- 可选：`useradd` 创建 `ops-reader`/`ops-writer` 降权用户

---

## 七、测试策略

### 7.1 测试覆盖要求

最低覆盖率：**80%**（含 P0 功能 100% 覆盖）

### 7.2 关键测试用例

| 测试类 | 关键场景 |
|--------|---------|
| `test_permission_manager.py` | `rm -rf /` 必须被 deny；`ls /tmp` 在 plan 模式下通过 |
| `test_intent_classifier.py` | CRITICAL 意图识别；UNKNOWN → LLM 兜底路径 |
| `test_hook_manager.py` | hook 崩溃时 fail-safe；exit 2 注入消息验证 |
| `test_circuit_breaker.py` | 3 次连续失败 → OPEN；HALF_OPEN 探测成功 → CLOSED |
| `test_snapshot.py` | 快照 → 删除文件 → 回滚 → 文件完整 |
| `test_agent_loop.py` | Mock LLM 集成测试：完整请求-响应-工具调用生命周期 |

---

## 八、参考文献与研究来源

### 学术与技术研究

1. **MiniScope: Least Privilege for Tool-Calling Agents** (UC Berkeley / IBM, 2025-12)  
   *核心启发*：从零权限出发，仅在需要新权限时向人类请求。OpsAgent 的 `PermissionManager` 4步管道体现此原则。  
   Source: https://arxiv.org/pdf/2512.11147

2. **AC4A: Access Control Framework for Agents** (arxiv 2603.20933)  
   *核心启发*：权限为 resource + action 元组（read/write/create），在运行时拦截层强制执行。  
   Source: https://www.arxiv.org/pdf/2603.20933

3. **Structured Concurrency for AI Pipelines** (Tianpan, 2026-04)  
   *核心启发*：`asyncio.TaskGroup` 替代 `asyncio.gather()` 防止孤儿 Task，OpsAgent 强制要求 Python 3.11+。  
   Source: https://tianpan.co/blog/2026-04-09-structured-concurrency-ai-pipelines-parallel-tool-calls

### API 文档

4. **DeepSeek Function Calling Official Docs**  
   *关键约束*：`deepseek-reasoner` 不支持 function calling，最多 32 tools/request。  
   Source: https://api-docs.deepseek.com/guides/function_calling

5. **DeepSeek Tool Calls Implementation Guide**  
   *工具调用格式*：与 OpenAI 完全兼容的 `tools` / `tool_choice` / `tool_call_id` 格式。  
   Source: https://chat-deep.ai/docs/deepseek-tool-calls/

### 架构参考

6. **learn-claude-code s01-s19 体系**（内部文档）  
   架构基础，详见 `/home/huishuohuademao/workspace/agent/learn-claude-code/`

7. **Securing AI Agents: Principle of Least Privilege**  
   *OS 级安全*：专用非特权 Linux 用户、`--cap-drop ALL`、auditd 防篡改日志。  
   Source: https://security.furybee.org/articles/securing-ai-agents
