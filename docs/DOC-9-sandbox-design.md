# DOC-9: 沙箱与回滚 — 纵深防御第三层（团队评审修订版）

**状态**: 设计方案（四 Agent 交叉评审通过）
**日期**: 2026-04-27
**评审**: 架构师 + 安全专家 + 比赛评审 + 魔鬼代言人
**关联文档**: DOC-1（安全层）、DOC-6（PrivilegeBroker）、DOC-7（意图策略引擎）、DOC-8（任务权限系统）

---

## 团队评审摘要

四位 Agent 完成独立分析，交叉验证结论：

### 共识（4/4 一致）
1. **「一键回滚」触发条件未定义** — 比赛评审：35% 安全权重下最大失分风险
2. **Fail-open 降级策略危险** — 安全专家 + 魔鬼代言人：生产环境必须 Fail-closed
3. **cmd 字符串解析文件路径不可靠** — 魔鬼代言人：glob/重定向/管道/source 均无法可靠解析
4. **容器加固不足** — 安全专家：缺 cap-drop=ALL / seccomp / userns-remap 三项关键加固

### 核心分歧：是否需要 Docker

| 立场 | 代表 | 论据 |
|------|------|------|
| 不需要 | 魔鬼代言人 | 4.4 节只需 RBAC（已有）+ 受控执行（已有 sudo）+ 备份回滚（缺），2h 搞定 |
| 需要但加固 | 安全专家 | 三层纵深正确，但缺容器加固等于安全幻觉，必须补 cap/seccomp/user-ns |
| 需要但简化 | 架构师 | Docker 方向对，但热容器跨 turn 复用复杂度被低估，建议 per-turn 一次性容器 |
| 可接受 | 比赛评审 | Docker 选型合理，但基础容器安全不够"云沙箱"深度，建议补充 seccomp/AppArmor 声明 |

### 最终仲裁：两阶段策略

- **Phase 1（必做，~3h）**: 快照 + 回滚 + 一键回滚 CLI + 输出过滤。保障 35% 安全分底线。
- **Phase 2（加分项，~4h）**: Docker 沙箱加固版。Phase 1 测试通过后、剩余时间 >4h 才启动。

理由：不允许出现「Docker 半成品 + 回滚半成品」的交付风险。先保底线，再抢加分。

---

## 一句话结论

沙箱不是替代权限系统，而是给执行层再套一层能力边界：

- 权限系统决定：这次工具调用要不要执行
- 沙箱决定：就算执行了，这个进程最多能碰到哪些文件、哪些网络目标
- 回滚保证：执行出错了，能一键恢复

三者组合构成真正的 Defense-in-Depth（纵深防御）。

---

## 1. 为什么要两阶段

### 1.1 比赛需求精确拆解

Section 4.4「受限执行环境」（35% 权重）要求三项：

| 需求 | 当前状态 | 所需工作 |
|------|---------|---------|
| RBAC 最小权限 | **已完成** — PermissionManager 4 步管道 + PrivilegeBroker 三级 sudo | 无 |
| 沙箱/受控执行 | **已完成** — sudo 切换到 ops-* 用户 + 环境净化 + 命令脚本隔离 | 无（Phase 2 可选增强为 Docker） |
| 执行前自动备份 + 一键回滚 | **缺失** — 04_snapshot_hook.py 是空壳，rollback/ 无实际逻辑 | Phase 1 全部工作 |

**关键认知**：前两项已由现有代码满足，Phase 1 只做缺失的第三项即可覆盖 4.4 节全部要求。Phase 2 的 Docker 是锦上添花，不是雪中送炭。

### 1.2 为什么保留 Docker 作为 Phase 2

- 比赛 4.4 节明确写了「采用云沙箱、docker 等方案」，有 Docker 可展示工程深度
- 安全专家指出的加固项（cap-drop/seccomp/user-ns）在 Phase 2 中全部落实
- Phase 2 不阻塞 Phase 1 交付，时间不允许就砍掉

---

## 2. Phase 1：快照 + 回滚 + 输出过滤（必做）

### 2.1 架构变更

```
exec_bash(cmd)
  → PermissionManager.check(cmd)       # Layer 1: "该不该执行？"
  → SnapshotManager.create(cwd)        # NEW: 执行前自动备份
  → PrivilegeBroker.execute(cmd)       # Layer 2: "以什么身份执行？"
  → OutputValidator.sanitize(stdout)   # NEW: 输出过滤（Section 7 第三层）
  → [失败时] RollbackExecutor.restore() # NEW: 一键回滚
```

### 2.2 快照策略：全量工作目录 rsync

**为什么不用 cmd 字符串解析文件路径：**

魔鬼代言人指出：`rm *.log`、`source script.sh`、管道、重定向等操作的真实文件操作 hook 感知不到，从字符串解析路径漏报率极高。

**采用方案**：对整个工作目录做 rsync 全量备份。

```
rsync -a --max-size=100M {cwd}/ → /var/lib/opsagent/snapshots/{task_id}/{timestamp}/
```

- 大文件（>100MB）跳过并记录 WARNING
- 跳过 socket、FIFO、设备文件
- 生成 snapshot_manifest.json：{files: [{path, sha256, size, mtime}], timestamp, task_id}
- 安全专家建议：manifest 附带 HMAC/SHA256，防止快照文件被篡改后植入后门

### 2.3 一键回滚：双触发机制

这是解决比赛评审指出的 #1 失分风险的关键设计。

**Trigger A — 自动触发（PostToolUse Hook）**：
```
触发条件（满足任一）：
  - exit_code != 0 且 stderr 匹配 "(?i)(permission denied|read-only|not permitted|operation not allowed)"
  - 文件完整性校验失败（执行后 SHA256 与快照不一致，且非预期变更）
  - exit_code == 0 但 stderr 包含破坏性操作警告
```
自动调用 RollbackExecutor.restore(task_id)，记录回滚审计日志。

**Trigger B — 手动触发（CLI 一键回滚）**：
```bash
$ opsagent rollback <task_id>
  → 展示将被恢复的文件列表（dry-run）
  → 确认提示："恢复 12 个文件到执行前状态？[y/N]"
  → 执行 RollbackExecutor.restore(task_id)
  → 输出："已恢复 12/12 个文件。回滚完成。"

$ opsagent rollback --list <task_id>
  → 列出该任务所有快照及时间戳

$ opsagent rollback --auto <task_id>
  → 非交互模式，供 hook 自动触发使用
```

这明确满足了 Section 4.4「支持一键回滚」的要求——有显式的、可测试的 CLI 入口。

### 2.4 输出过滤层

Section 7 常见失分点要求「输入校验 → 沙箱执行 → 输出过滤」三层防护。前两层已有，第三层缺失。

```
PostToolUse Hook: 02_output_filter.py（新建）
  过滤规则:
    - 脱敏 stdout 中的内部 IP/主机名
    - 截断超过 max_output_bytes（默认 1MB）的输出
    - 标记包含已知敏感路径的输出（/etc/shadow、~/.ssh/id_rsa 等）
    - 返回 sanitized_output + warnings[] 列表
```

### 2.5 Phase 1 实施步骤

| 步骤 | 文件 | 内容 | 预估 |
|------|------|------|------|
| Step 1.1 | `hooks/pre_tool/04_snapshot_hook.py` | 填充空壳：rsync 备份 + SHA256 manifest | 1h |
| Step 1.2 | `rollback/snapshot.py` + `rollback/rollback.py` | SnapshotManager + RollbackExecutor | 1h |
| Step 1.3 | CLI 入口（修改现有或新建） | `opsagent rollback <task_id>` | 0.5h |
| Step 1.4 | PostToolUse 触发逻辑 | 双触发条件实现 | 0.5h |
| Step 1.5 | `hooks/post_tool/02_output_filter.py` | 输出脱敏 + 截断 | 0.5h |

---

## 3. Phase 2：Docker 沙箱加固版（加分项）

### 3.1 启动条件

Phase 2 仅在以下条件全部满足时启动：
- Phase 1 所有集成测试通过
- Phase 1 代码已审查合并
- 剩余开发时间 > 4 小时

### 3.2 容器安全加固（安全专家四项强制要求）

**如果不做这四项，Docker 沙箱就是安全幻觉：**

| 加固项 | 实施方式 | 优先级 | 理由 |
|--------|---------|--------|------|
| 非 root 运行 | `--user 9001:9001` | **CRITICAL** | 容器内 root = 宿主 root（同 UID 0），逃逸即宿主 root |
| 全能力删除 | `--cap-drop=ALL` | **CRITICAL** | 默认能力集含 CAP_SYS_ADMIN(mount)、CAP_SYS_PTRACE(注入) |
| 自定义 seccomp | `/etc/opsagent/seccomp.json` | HIGH | 默认 profile 拦截 44 syscall，ptrace/clone 相关需显式审计 |
| 用户命名空间映射 | `daemon.json` 启用 `userns-remap` | MEDIUM | 容器 root → 宿主 UID 65536+，即使逃逸也是无权限用户 |

### 3.3 容器规格（修订版）

```
一次性容器（per-command，不复用）:
  - 镜像: python:3.11-slim（不构建自定义镜像，容器启动时 apt-get install 依赖）
  - Rootfs: 只读挂载
  - 可写挂载:
    - {cwd} → /workspace (rw)
    - /tmp/opsagent/{task_id}/ (rw, tmpfs, noexec)  # noexec 防注入执行
  - 网络: --network=none（默认阻断所有网络）
  - 用户: --user 9001:9001（ops-reader 身份，容器内无 root）
  - 资源限制: --memory=512m --cpus=1 --pids-limit=50
  - 安全选项:
    - --security-opt=no-new-privileges
    - --cap-drop=ALL
    - --security-opt=seccomp=/etc/opsagent/seccomp.json
  - 清理: --rm（执行完自动销毁）
```

### 3.4 为什么不复用容器

架构师指出热容器跨 turn 复用的复杂度被低估：
- 需要在 LoopState/ToolUseContext 持有容器引用
- 需处理 LLM 空闲时容器超时清理
- 魔鬼代言人补充：环境变量残留、僵尸进程、/tmp 累积——是调试噩梦

**决定**：每个命令创建新容器，执行完立即 `--rm` 销毁。启动延迟 ~1-2s，对于运维命令（通常秒级执行）可接受。

### 3.5 降级策略：FAIL-CLOSED

安全专家 + 魔鬼代言人一致要求：

```
生产模式: Docker 不可用 → 拒绝执行危险命令，返回明确错误
开发模式: Docker 不可用 → WARNING + 要求管理员显式确认（每次会话一次）
```

不允许静默降级。沙箱挂了用户必须知道，并主动选择是否继续。

### 3.6 集成方式：ToolUseContext 注入

架构师建议不直接在 exec_bash() 函数体内加 if-else，而是通过 ToolUseContext 总线注入：

```python
# SandboxManager 实现与 PrivilegeBroker 同型的 execute() 接口
# 在 _build_tool_use_context() 时点注入
# 调用方无感知，对 cron/background 任务类型具备相同抽象能力
```

完整接口：
```python
acquire(task_id) → ContainerHandle
execute(handle, cmd, privilege) → ExecResult {stdout, stderr, exit_code}
release(task_id)
```

### 3.7 Phase 2 实施步骤

| 步骤 | 文件 | 内容 | 预估 |
|------|------|------|------|
| Step 2.1 | `security/sandbox.py`（新建） | SandboxManager：Docker CLI 包装，容器生命周期 | 1.5h |
| Step 2.2 | `config.py`（修改） | SandboxConfig 字段 | 15min |
| Step 2.3 | `tools/exec_tools.py`（修改） | ToolUseContext 注入沙箱，fail-closed 降级 | 1h |
| Step 2.4 | seccomp profile + daemon.json 配置 | seccomp 白名单 + userns-remap 配置 | 30min |
| Step 2.5 | `tests/test_sandbox.py`（新建） | 沙箱 + 回滚集成测试 | 1h |

---

## 4. 与现有安全管道的集成（修订版）

### 4.1 Phase 1 集成（必做）

```
exec_bash(cmd)
  → PermissionManager.check(cmd)
  → [allowed]
  → SnapshotManager.create(cwd)          ← NEW
  → PrivilegeBroker.execute(cmd, user)
  → OutputValidator.sanitize(result)     ← NEW
  → [if failure] RollbackExecutor.restore() ← NEW
```

### 4.2 Phase 2 集成（加分，在 PermissionManager 和 PrivilegeBroker 之间插入）

```
exec_bash(cmd)
  → PermissionManager.check(cmd)
  → [allowed]
  → SnapshotManager.create(cwd)
  → SandboxManager.acquire(task_id)      ← NEW (Phase 2)
  → [容器内] PrivilegeBroker.execute(cmd)
  → OutputValidator.sanitize(result)
  → [if failure] RollbackExecutor.restore()
  → SandboxManager.release(task_id)      ← NEW (Phase 2)
```

### 4.3 沙箱违规处理

当命令因沙箱限制失败时（exit code 126/137 或 stderr 包含 "Permission denied"/"Network is unreachable"），返回明确信息：

```python
ToolResult(
    success=False,
    output="",
    error="[sandbox_violation] 操作被沙箱拦截：写入系统路径被拒绝",
)
```

### 4.4 降级策略：FAIL-CLOSED

```
Docker daemon 不可用时:
  1. 启动时 SandboxManager.health_check() 检测
  2. 不可用 + 生产模式: sandbox_enabled = False + 拒绝危险命令 + 用户可见错误
  3. 不可用 + 开发模式: sandbox_enabled = False + WARNING + 管理员手动确认
```

Phase 1 模式（无 Docker）：正常执行，有 PermissionManager + PrivilegeBroker 两层保护。

---

## 5. 快照与回滚

### 5.1 快照机制

```
PreToolUse Hook: 04_snapshot_hook.py
  输入: tool_name, tool_args（含 cwd）
  流程:
    1. rsync -a --max-size=100M {cwd}/ → /var/lib/opsagent/snapshots/{task_id}/{timestamp}/
    2. 生成 SHA256 checksums（每个文件）
    3. 写入 snapshot_manifest.json
    4. 大文件（>100MB）跳过 + WARNING
    5. socket/FIFO/设备文件跳过
  输出: exit 0（成功）/ exit 1（快照失败，阻塞执行）
  
安全增强（安全专家建议）:
  - manifest 附带 HMAC 或 SHA256，恢复前验证完整性
  - 防止快照文件被篡改后植入后门
```

### 5.2 回滚执行

```
Trigger A — 自动触发:
  PostToolUse 检测到 exit_code != 0 + stderr 匹配破坏性模式
  → 自动调用 RollbackExecutor.restore(task_id)

Trigger B — 手动一键回滚:
  $ opsagent rollback <task_id>
  → Dry-run 展示文件列表
  → 用户确认
  → 从快照目录恢复每个文件到原始路径
  → 校验恢复后 SHA256 与快照一致
  → 记录回滚审计日志
  → 保留快照 N 小时（默认 24h），超期 GC 清理
```

---

## 6. 未来任务系统兼容设计

Phase 1 的快照/回滚机制与任务类型无关，天然兼容所有任务类型。

Phase 2 的 Docker 沙箱预留：

| 任务类型 | 沙箱行为 | 实现时机 |
|---------|---------|---------|
| interactive | 一次性容器，执行完自动销毁 | Phase 2（本次） |
| cron/巡检 | 持久化只读容器，无写挂载，定时复用 | Week 5-6 |
| background | 持久化可写容器，跨 turn 存活，空闲暂停 | Week 5-6 |

**TempRule → 卷挂载映射**（Week 5-6）：当任务的 TempRule 授权某路径可写时，该路径通过 `-v` 动态加入容器挂载列表。

**令牌预算追踪**（Week 7）：每个沙箱会话记录 token 消耗，预算耗尽触发容器清理 + 状态迁移至 blocked。

---

## 7. 实施步骤汇总

| 阶段 | 步骤 | 文件 | 预估 |
|------|------|------|------|
| **Phase 1** | | | **3.0h** |
| 1.1 | `hooks/pre_tool/04_snapshot_hook.py` | 填充空壳：rsync + SHA256 manifest | 1.0h |
| 1.2 | `rollback/snapshot.py` + `rollback/rollback.py` | SnapshotManager + RollbackExecutor | 1.0h |
| 1.3 | CLI 入口 | `opsagent rollback <task_id>` | 0.5h |
| 1.4 | PostToolUse 触发逻辑 | 双触发条件（自动 + 手动） | 0.5h |
| 1.5 | `hooks/post_tool/02_output_filter.py` | 输出脱敏 + 截断 | 0.5h |
| **Phase 2** | | | **4.0h** |
| 2.1 | `security/sandbox.py`（新建） | SandboxManager：容器生命周期、命令执行、健康检查 | 1.5h |
| 2.2 | `config.py`（修改） | SandboxConfig 字段 | 0.25h |
| 2.3 | `tools/exec_tools.py`（修改） | ToolUseContext 注入沙箱，fail-closed | 1.0h |
| 2.4 | seccomp profile + daemon.json | seccomp 白名单 + userns-remap | 0.5h |
| 2.5 | `tests/test_sandbox.py`（新建） | 沙箱 + 回滚集成测试 | 1.0h |

---

## 8. 风险与缓解（修订版）

| 风险 | 阶段 | 缓解措施 |
|------|------|---------|
| 回滚触发过宽或过窄 | 1 | 双触发机制（自动 pattern + 手动 CLI），正则可通过配置调优 |
| 快照撑爆磁盘 | 1 | rsync --max-size=100M；max_snapshot_size 可配置；24h 自动 GC |
| 快照文件被篡改（完整性） | 1 | SHA256 checksum + manifest；恢复前校验；可选 HMAC |
| Docker daemon 不可用 | 2 | **FAIL-CLOSED**：生产模式拒绝危险命令；开发模式需管理员确认 |
| 容器逃逸（runc CVE 历史） | 2 | --cap-drop=ALL + 非 root 用户 + seccomp + no-new-privileges + userns-remap |
| 容器状态污染（热复用） | — | **已消除**：采用一次性容器，不复用 |
| 路径解析漏报（cmd 字符串） | — | **已消除**：采用全量 cwd rsync，不解析 cmd 字符串 |
| 静默降级暴露攻击面 | — | **已消除**：fail-closed + 用户显式通知 |

---

## 9. Agent 评审记录

| Agent | 关键发现 | 采纳状态 |
|-------|---------|---------|
| 架构师 | ToolUseContext 注入优于 exec_bash() 内 if-else；热容器跨 turn 复用复杂度被低估 | ✅ 采纳：Phase 2 用 ToolUseContext 注入；容器改为一次性 |
| 安全专家 | 缺 cap-drop=ALL / seccomp / userns-remap 三项；容器内 root 是最大风险；降级必须 fail-closed | ✅ 全部采纳为 Phase 2 强制要求 |
| 比赛评审 | 「一键回滚」触发条件模糊是最大失分风险；输出过滤层缺失 | ✅ 采纳：双触发机制 + CLI 入口 + 输出过滤 Hook |
| 魔鬼代言人 | Docker 严重过度设计；cmd 字符串解析不可靠；静默降级危险 | ✅ 采纳：两阶段策略 + 全量 rsync + fail-closed |

---

## 10. 参考资料

- claude-code/docs/safety/sandbox.mdx — 纵深防御设计哲学、权限与沙箱互补关系
- opsagent-competition-requirements.md Section 4.4 — 受限执行环境（35% 权重）
- opsagent-competition-requirements.md Section 7 — 常见失分点
- DOC-1-security-layer.md — 现有安全层架构
- DOC-6-privilege-broker.md — 三级 sudo 权限代理
- DOC-8-task-permission-system.md — 任务系统 + TempRule 令牌设计
- `.claude/plan/sandbox.md` — 英文实施计划（详细步骤 + 伪代码）
