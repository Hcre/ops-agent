# 日志分析技能

## 适用场景
故障排查、异常检测、安全审计。

## 操作步骤

### 1. 系统日志
```bash
journalctl -p err -n 100         # 最近 100 条错误
journalctl -u <service> -n 50    # 指定服务日志
journalctl --since "1 hour ago"  # 最近 1 小时
```

### 2. 应用日志
```bash
tail -n 100 /var/log/nginx/error.log
grep -i "error\|exception\|fatal" /var/log/app/*.log | tail -50
```

### 3. 安全日志
```bash
tail -n 100 /var/log/auth.log    # 认证日志
grep "Failed password" /var/log/auth.log | tail -20  # 登录失败
last -n 20                       # 最近登录记录
```

## 分析模式
- 时间序列：按时间排序，找到异常发生的时间点
- 频率分析：统计错误出现频率，识别高频问题
- 关联分析：对比多个服务日志，找到因果关系

## 注意事项
- 日志文件只读，不需要确认
- 大文件使用 tail/grep，避免 cat 整个文件
