# 磁盘清理技能

## 适用场景
磁盘使用率 > 80%，需要释放空间。

## 操作步骤

### 1. 诊断阶段（只读，无需确认）
```bash
df -h                          # 查看各分区使用率
du -sh /var/log/*              # 日志目录大小
du -sh /tmp/*                  # 临时文件大小
find /var/log -name "*.gz" -mtime +7  # 7天前的压缩日志
```

### 2. 清理候选（需要确认）
- `/var/log/*.gz` — 压缩日志（超过 7 天）
- `/tmp/*` — 临时文件（超过 3 天）
- `journalctl --vacuum-size=500M` — 限制 journal 大小

### 3. 风险评估
- HIGH: 删除数据库日志（需确认）
- MEDIUM: 删除应用日志（需确认）
- LOW: 删除系统临时文件

## 注意事项
- 删除前必须创建快照
- 数据库相关日志（mysql/postgres）需要 DBA 确认
- 不要删除 /var/log/auth.log（安全审计）
