# 进程管理技能

## 适用场景
进程异常（CPU/内存占用过高、僵尸进程、服务崩溃）。

## 操作步骤

### 1. 诊断阶段（只读）
```bash
ps aux --sort=-%cpu | head -20   # CPU 占用 Top 20
ps aux --sort=-%mem | head -20   # 内存占用 Top 20
ps aux | grep Z                  # 僵尸进程
systemctl list-units --failed    # 失败的服务
```

### 2. 处理操作（需要确认）
```bash
kill -15 <pid>                   # 优雅终止（SIGTERM）
kill -9 <pid>                    # 强制终止（SIGKILL，最后手段）
systemctl restart <service>      # 重启服务
```

### 3. 风险评估
- CRITICAL: kill 数据库进程
- HIGH: kill 业务服务进程
- MEDIUM: restart 非核心服务
- LOW: kill 僵尸进程

## 注意事项
- 优先使用 SIGTERM，等待 30s 后再考虑 SIGKILL
- 重启前确认服务有自动恢复机制（systemd Restart=on-failure）
