# OpsAgent

面向 Linux 企业运维的安全 AI Agent，基于 s01-s19 架构哲学。  
模型：DeepSeek-R1 / Qwen3（OpenAI 兼容接口）｜架构：Hook 外置安全管道 + 最小权限执行 + 三级回滚

## 模型路由规则（省钱）

**Haiku 做，不用问**：读文件、搜索代码、查目录结构、运行测试、格式检查、写简单 stub  
**Sonnet（主模型）做**：架构决策、复杂调试、跨文件设计、用户明确要求推理的任务

```
凡是 Read / Grep / Glob / Bash（只读命令）→ 派 model: haiku 的 Agent subagent
凡是需要判断"怎么做"→ 主模型自己来
```

## 目录速查

| 目录 | 职责 | 对应 s 编号 |
|------|------|------------|
| `core/` | 主循环 / 上下文压缩 / Hook / 记忆 / 调度 / 错误恢复 | s01,s06,s08-s14,s17,s18 |
| `hooks/` | PreToolUse / PostToolUse 外置安全脚本 | s08 |
| `security/` | PermissionManager / IntentClassifier / PrivilegeBroker | s07 + OpsAgent |
| `perception/` | 磁盘 / 进程 / 网络 / 日志 OS 感知 | s01 感知端 |
| `tools/` | 工具注册 + MCP 接入（ToolUseContext 总线）| s02, s02a, s19 |
| `managers/` | SQLite 持久化 / TaskRecord（工作图）/ RuntimeTaskState（执行槽）/ 审计日志 | s03, s12, s13 |
| `rollback/` | 快照 / 补偿注册表 / 恢复策略 | OpsAgent |
| `teams/` | Analyst / Executor / Auditor 多角色协作 | s15, s16 |
| `skills/` | 按需加载的运维手册（SKILL.md）| s05 |
| `docs/` | DESIGN.md / DOC-1~4 / IMPLEMENTATION_PLAN.md | — |

## 关键约束（从代码看不出来的）

- `deepseek-reasoner`（R1 思维链）**不支持 function calling**，工具调用必须用 `deepseek-chat`
- 安全审查员用 `qwen3-8b`，与主 Agent **完全隔离上下文**，不共享对话历史
- `LoopState.transition_reason` 必须显式赋值，不能只写 `continue`（见 `s00c`）
- `TaskRecord`（工作图目标）≠ `RuntimeTaskState`（执行槽位），不能混用（见 `s13a`）
- 工具执行通过 `ToolUseContext` 总线传递共享环境，不直接访问全局变量（见 `s02a`）
- 所有写操作必须经过 `PermissionManager.check()`，不得绕过

## 参考资料

遇到设计问题先查 `docs/`（本项目设计文档），再查 learn-claude-code（路径：`../learn-claude-code/docs/zh/`）。

| 文件 | 何时查 |
|------|--------|
| `s00-architecture-overview.md` | 整体架构迷失时 |
| `s00a-query-control-plane.md` | 理解 LoopState / QueryState 设计时 |
| `s00c-query-transition-model.md` | 修改主循环 / transition_reason 时 |
| `s02a-tool-control-plane.md` | 修改工具执行层 / ToolUseContext 时 |
| `s10a-message-prompt-pipeline.md` | 修改 SystemPromptBuilder 时 |
| `s13a-runtime-task-model.md` | 修改 task_manager / 后台任务时 |
| `s19a-mcp-capability-layers.md` | 扩展 MCP 接入层时 |
| `data-structures.md` | 新增任何 dataclass 前先对照标准形状 |
| `entity-map.md` | 概念边界混淆时（Todo/Task/RuntimeTask/Subagent）|


| 目录 | 职责 | 对应 s 编号 |
|------|------|------------|
| `core/` | 主循环 / 上下文压缩 / Hook / 记忆 / 调度 / 错误恢复 | s01,s06,s08-s14,s17,s18 |
| `hooks/` | PreToolUse / PostToolUse 外置安全脚本 | s08 |
| `security/` | PermissionManager / IntentClassifier / PrivilegeBroker | s07 + OpsAgent |
| `perception/` | 磁盘 / 进程 / 网络 / 日志 OS 感知 | s01 感知端 |
| `tools/` | 工具注册 + MCP 接入（ToolUseContext 总线）| s02, s02a, s19 |
| `managers/` | SQLite 持久化 / TaskRecord（工作图）/ RuntimeTaskState（执行槽）/ 审计日志 | s03, s12, s13 |
| `rollback/` | 快照 / 补偿注册表 / 恢复策略 | OpsAgent |
| `teams/` | Analyst / Executor / Auditor 多角色协作 | s15, s16 |
| `skills/` | 按需加载的运维手册（SKILL.md）| s05 |
| `docs/` | DESIGN.md / DOC-1~4 / IMPLEMENTATION_PLAN.md | — |

## 关键约束（从代码看不出来的）

- `deepseek-reasoner`（R1 思维链）**不支持 function calling**，工具调用必须用 `deepseek-chat`
- 安全审查员用 `qwen3-8b`，与主 Agent **完全隔离上下文**，不共享对话历史
- `LoopState.transition_reason` 必须显式赋值，不能只写 `continue`（见 `s00c`）
- `TaskRecord`（工作图目标）≠ `RuntimeTaskState`（执行槽位），不能混用（见 `s13a`）
- 工具执行通过 `ToolUseContext` 总线传递共享环境，不直接访问全局变量（见 `s02a`）
- 所有写操作必须经过 `PermissionManager.check()`，不得绕过

## 参考资料

遇到设计问题先查 `docs/`（本项目设计文档），再查 learn-claude-code（路径：`../learn-claude-code/docs/zh/`）。

| 文件 | 何时查 |
|------|--------|
| `s00-architecture-overview.md` | 整体架构迷失时 |
| `s00a-query-control-plane.md` | 理解 LoopState / QueryState 设计时 |
| `s00c-query-transition-model.md` | 修改主循环 / transition_reason 时 |
| `s02a-tool-control-plane.md` | 修改工具执行层 / ToolUseContext 时 |
| `s10a-message-prompt-pipeline.md` | 修改 SystemPromptBuilder 时 |
| `s13a-runtime-task-model.md` | 修改 task_manager / 后台任务时 |
| `s19a-mcp-capability-layers.md` | 扩展 MCP 接入层时 |
| `data-structures.md` | 新增任何 dataclass 前先对照标准形状 |
| `entity-map.md` | 概念边界混淆时（Todo/Task/RuntimeTask/Subagent）|
