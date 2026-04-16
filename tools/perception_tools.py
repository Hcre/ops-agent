"""
tools/perception_tools.py — 感知按需查询工具

LLM 看到感知摘要后，通过这些工具按需获取详细数据。
所有工具支持 mode 参数：summary（默认）/ detail / full
"""
from __future__ import annotations

import asyncio
import shlex
import time
import uuid
from typing import Literal, Optional

from core.agent_loop import ToolResult

Mode = Literal["summary", "detail", "full"]

# get_disk_detail 缓存：key=mount, value=(ts, output_str)
_disk_cache: dict[str, tuple[float, str]] = {}
_DISK_CACHE_TTL = 60.0


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

async def _run(cmd: str, timeout: int = 10) -> tuple[bool, str, int]:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            code = proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            return False, "Command timed out", -1
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        return (code == 0), (out if code == 0 else err or out), code
    except Exception as e:
        return False, str(e), -1


def _result(name: str, t0: float, success: bool, output: str, error: str = "") -> ToolResult:
    return ToolResult(
        tool_call_id=str(uuid.uuid4())[:8],
        tool_name=name,
        success=success,
        output=output,
        error=error,
        elapsed_ms=(time.monotonic() - t0) * 1000,
        exit_code=0 if success else -1,
    )


# ---------------------------------------------------------------------------
# get_disk_detail
# ---------------------------------------------------------------------------

async def get_disk_detail(path: str = "/", mode: Mode = "summary") -> ToolResult:
    """
    返回指定路径所在挂载点的磁盘信息。
    path 可以是任意路径，工具内部自动定位所属挂载点。
    summary: 使用率 + inode + Top 10 目录
    detail:  追加 iostat IO 统计
    full:    追加 lsof 写入进程
    """
    t0 = time.monotonic()
    safe_path = shlex.quote(path)

    # 定位挂载点
    ok, mount_out, _ = await _run(f"df -P {safe_path} | awk 'NR==2{{print $6}}'")
    mount = mount_out.strip() if ok and mount_out.strip() else path
    safe_mount = shlex.quote(mount)

    # summary 级别命中缓存
    if mode == "summary":
        cached = _disk_cache.get(mount)
        if cached and (time.monotonic() - cached[0]) < _DISK_CACHE_TTL:
            return _result("get_disk_detail", t0, True, cached[1])

    # 空间 + inode（必采）
    ok_df, df_out, _ = await _run(f"df -h {safe_mount} && echo '---' && df -i {safe_mount}")
    if not ok_df:
        return _result("get_disk_detail", t0, False, "", f"df 失败: {df_out}")

    # Top 10 目录（--one-file-system 防止跨挂载点卡死）
    if mount == "/":
        du_cmd = (
            "du -h --max-depth=1 --one-file-system / 2>/dev/null"
            " | grep -v -E '^[0-9.]+[KMGTPEZYkMGTPEZY]?\\s+/(proc|sys|dev|run)$'"
            " | sort -hr | head -n 10"
        )
    else:
        du_cmd = f"du -h --max-depth=1 --one-file-system {safe_mount} 2>/dev/null | sort -hr | head -n 10"

    _, du_out, _ = await _run(du_cmd, timeout=10)

    sections = [
        f"--- 空间 + Inode (挂载点: {mount}) ---\n{df_out}",
        f"--- Top 10 目录 (depth=1) ---\n{du_out or '（无数据）'}",
    ]

    if mode in ("detail", "full"):
        _, io_out, _ = await _run("iostat -dx 1 1 2>/dev/null | tail -n 8")
        sections.append(f"--- IO 统计 ---\n{io_out or '（iostat 不可用）'}")

    if mode == "full":
        _, lsof_out, _ = await _run(
            f"lsof +D {safe_mount} 2>/dev/null | awk '{{print $1,$2,$9}}' | sort | uniq -c | sort -rn | head -n 15"
        )
        sections.append(f"--- 写入进程 (lsof) ---\n{lsof_out or '（无数据或权限不足）'}")

    output = "\n\n".join(sections)
    if mode == "summary":
        _disk_cache[mount] = (time.monotonic(), output)

    return _result("get_disk_detail", t0, True, output)


# ---------------------------------------------------------------------------
# get_process_detail
# ---------------------------------------------------------------------------

async def get_process_detail(
    pid: Optional[int] = None,
    name: Optional[str] = None,
    mode: Mode = "summary",
) -> ToolResult:
    """
    返回进程详细信息。
    - name 匹配多个时返回列表，让 LLM 选择具体 pid 再查。
    - pid 不存在时自动检查 dmesg OOM 记录。
    summary: 基本信息 + FD 数量
    detail:  追加打开文件列表
    full:    追加内存映射
    """
    t0 = time.monotonic()

    # name 模式：先列出所有匹配
    if name and not pid:
        safe_name = shlex.quote(name)
        ok, pgrep_out, _ = await _run(f"pgrep -f {safe_name}")
        if not ok or not pgrep_out.strip():
            _, oom_out, _ = await _run(
                f"dmesg --time-format reltime 2>/dev/null"
                f" | grep -i 'oom\\|killed process' | grep -i {safe_name} | tail -n 5"
            )
            oom = oom_out.strip() or "无 OOM 记录"
            return _result("get_process_detail", t0, False,
                           f"--- OOM / Kill 记录 ---\n{oom}",
                           f"找不到进程: {name}")

        pids = [p.strip() for p in pgrep_out.strip().splitlines() if p.strip().isdigit()]
        if len(pids) > 1:
            pid_csv = ",".join(pids[:20])
            _, ps_out, _ = await _run(
                f"ps -p {pid_csv} -o pid,user,%cpu,%mem,etime,command --no-headers 2>/dev/null"
            )
            return _result("get_process_detail", t0, True,
                           f"找到 {len(pids)} 个匹配进程，请用 pid 参数指定：\n\n"
                           f"{'PID':>7}  {'USER':<10} {'%CPU':>5} {'%MEM':>5}  {'ELAPSED':<12}  COMMAND\n"
                           f"{ps_out}")
        pid = int(pids[0])

    if not pid:
        return _result("get_process_detail", t0, False, "", "缺少 pid 或 name 参数")

    str_pid = str(int(pid))

    # 检查进程是否存活
    _, _, alive_code = await _run(f"kill -0 {str_pid} 2>&1")
    if alive_code != 0:
        search = shlex.quote(name) if name else str_pid
        _, oom_out, _ = await _run(
            f"dmesg --time-format reltime 2>/dev/null"
            f" | grep -i 'oom\\|killed process' | grep -i {search} | tail -n 5"
        )
        oom_section = f"--- OOM / Kill 记录 ---\n{oom_out.strip() or '无记录'}"
        new_section = ""
        if name:
            safe_name = shlex.quote(name)
            _, new_pids, _ = await _run(f"pgrep -f {safe_name}")
            if new_pids.strip():
                new_section = f"\n--- 同名进程（可能已重启）---\nPIDs: {new_pids.strip()}"
        return _result("get_process_detail", t0, False,
                       oom_section + new_section,
                       f"PID {str_pid} 已不存在（可能已崩溃或重启）")

    # 基本信息（summary 必采）
    tasks = [
        _run(f"ps -p {str_pid} -o pid,ppid,user,stat,start,etime,%cpu,%mem,vsz,rss,command"),
        _run(f"ls /proc/{str_pid}/fd 2>/dev/null | wc -l"),
    ]
    (ok_ps, ps_out, _), (_, fd_count, _) = await asyncio.gather(*tasks)
    if not ok_ps:
        return _result("get_process_detail", t0, False, "", f"ps 失败: {ps_out}")

    sections = [
        f"--- 进程信息 ---\n{ps_out}\nFD 数量: {fd_count}",
    ]

    if mode in ("detail", "full"):
        _, lsof_out, _ = await _run(f"lsof -p {str_pid} 2>/dev/null | head -n 30")
        sections.append(f"--- 打开文件 (Top 30) ---\n{lsof_out or '（无数据或权限不足）'}")

    if mode == "full":
        _, maps_out, _ = await _run(
            f"cat /proc/{str_pid}/maps 2>/dev/null | awk '{{print $6}}' | sort | uniq -c | sort -rn | head -n 20"
        )
        sections.append(f"--- 内存映射 (Top 20) ---\n{maps_out or '（无数据）'}")

    return _result("get_process_detail", t0, True, "\n\n".join(sections))


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------

def _detect_stacktrace(lines: list[str], n: int) -> list[str]:
    """检测到 stacktrace 特征且行数达到上限时，附加截断提示"""
    MARKERS = ("Traceback", "Exception", "Caused by", '  File "', "\tat ")
    if len(lines) >= n and any(any(m in l for m in MARKERS) for l in lines):
        lines.append("[提示] 日志可能被截断，stacktrace 不完整，建议增大 n 或缩小 since 范围")
    return lines


async def get_logs(
    level: str = "err",
    n: int = 50,
    keyword: Optional[str] = None,
    since: str = "10 minutes ago",
    unit: Optional[str] = None,
    mode: Mode = "summary",
) -> ToolResult:
    """
    返回过滤后的日志行（journalctl）。
    unit: systemd 服务名（-u 参数），如 nginx、mysql。
    summary: 最近 n 条过滤日志
    detail:  追加上下文行（-C 3）
    full:    不限行数，返回完整时间窗口
    """
    t0 = time.monotonic()

    actual_n = n if mode != "full" else 200

    parts = [
        "journalctl",
        f"-p {shlex.quote(level)}",
        f"--since {shlex.quote(since)}",
        f"-n {int(actual_n)}",
        "--no-pager",
    ]
    if unit:
        parts.append(f"-u {shlex.quote(unit)}")
    if mode == "detail":
        parts.append("--output=short-precise")

    cmd = " ".join(parts)
    if keyword:
        cmd += f" | grep -i {shlex.quote(keyword)}"

    ok, out, code = await _run(cmd, timeout=15)
    if not ok:
        return _result("get_logs", t0, False, "", out)

    lines = out.splitlines()
    lines = _detect_stacktrace(lines, actual_n)

    return _result("get_logs", t0, True, "\n".join(lines))


# ---------------------------------------------------------------------------
# get_network_detail
# ---------------------------------------------------------------------------

async def _sample_rate(interface: Optional[str]) -> str:
    """间隔 1s 采样 /proc/net/dev，返回各接口实时速率"""
    grep_arg = (
        f"grep {shlex.quote(interface + ':')}"
        if interface
        else "grep -v -E 'lo:|Inter|face'"
    )
    _, s1, _ = await _run(f"cat /proc/net/dev | {grep_arg}")
    await asyncio.sleep(1)
    _, s2, _ = await _run(f"cat /proc/net/dev | {grep_arg}")

    s1_map = {l.split(":")[0].strip(): l for l in s1.splitlines() if ":" in l}
    lines = []
    for line in s2.splitlines():
        if ":" not in line:
            continue
        iface = line.split(":")[0].strip()
        if iface not in s1_map:
            continue
        try:
            f1 = s1_map[iface].split(":")[1].split()
            f2 = line.split(":")[1].split()
            rx = (int(f2[0]) - int(f1[0])) / 1024
            tx = (int(f2[8]) - int(f1[8])) / 1024
            rx_err = int(f2[2])
            tx_err = int(f2[10])
            rx_drop = int(f2[3])
            # 有错误/丢包时高亮
            flag = " ⚠️" if (rx_err + tx_err + rx_drop) > 0 else ""
            lines.append(
                f"  {iface:<12} RX {rx:>8.1f} KB/s  TX {tx:>8.1f} KB/s"
                f"  errors={rx_err+tx_err} drops={rx_drop}{flag}"
            )
        except (IndexError, ValueError):
            pass
    return "\n".join(lines) if lines else "  无法计算速率"


def _parse_tcp_states(ss_out: str) -> str:
    """从 ss -s 提取 TCP 状态统计"""
    lines = [
        f"  {l.strip()}"
        for l in ss_out.splitlines()
        if any(kw in l for kw in ("TCP:", "estab", "closed", "timewait", "LISTEN"))
    ]
    return "\n".join(lines) if lines else ss_out[:200]


async def get_network_detail(
    interface: Optional[str] = None,
    mode: Mode = "summary",
) -> ToolResult:
    """
    返回网络详情。interface=None 时返回所有接口汇总。
    summary: 实时速率（含 errors/drops）+ TCP 状态 + 监听端口
    detail:  追加接口完整统计（ip -s link）
    full:    追加 ss 连接详情
    """
    t0 = time.monotonic()
    iface_arg = f" {shlex.quote(interface)}" if interface else ""

    tasks: list = [
        _run("ss -s"),
        _run("ss -lntp 2>/dev/null"),
        _sample_rate(interface),
    ]
    (_, ss_sum, _), (_, listen_out, _), rate_out = await asyncio.gather(*tasks)

    sections = [
        f"--- 实时速率 (1s 采样) ---\n{rate_out}",
        f"--- TCP 状态 ---\n{_parse_tcp_states(ss_sum)}",
        f"--- 监听端口 ---\n{listen_out or '（无数据或权限不足）'}",
    ]

    if mode in ("detail", "full"):
        _, ip_out, _ = await _run(f"ip -s link show{iface_arg}")
        sections.append(f"--- 接口统计 (ip -s link) ---\n{ip_out}")

    if mode == "full":
        _, ss_full, _ = await _run(f"ss -tnp{iface_arg} 2>/dev/null | head -n 50")
        sections.append(f"--- 连接详情 (Top 50) ---\n{ss_full}")

    return _result("get_network_detail", t0, True, "\n\n".join(sections))


# ---------------------------------------------------------------------------
# get_system_snapshot
# ---------------------------------------------------------------------------

async def get_system_snapshot(mode: Mode = "summary") -> ToolResult:
    """
    返回压缩的全量系统快照。
    summary: uptime + load + 内存 + 磁盘告警 + Top 5 进程
    detail:  追加网络速率 + 监听端口
    full:    追加各挂载点详情
    """
    t0 = time.monotonic()

    cmds = {
        "uptime":    "uptime",
        "loadavg":   "cat /proc/loadavg",
        "mem":       "free -h | grep -E 'Mem|Swap'",
        "disk_warn": "df -h | awk 'NR==1 || $5+0 > 80' | grep -v tmpfs",
        "top_procs": "ps aux --sort=-%cpu | head -n 6 | tail -n 5",
    }
    keys = list(cmds.keys())
    results = await asyncio.gather(*[_run(c) for c in cmds.values()])
    data = {k: (r[1] if r[0] else f"[error: {r[1]}]") for k, r in zip(keys, results)}

    # 权限探测
    warnings: list[str] = []
    _, ss_test, ss_code = await _run("ss -lntp 2>&1 | head -n 1")
    if ss_code != 0 or "Permission denied" in ss_test:
        warnings.append("ss -lntp: 进程名不可见（需要 root）")
    _, _, fd_code = await _run("ls /proc/1/fd 2>&1 | head -n 1")
    if fd_code != 0:
        warnings.append("/proc/[pid]/fd: 其他用户进程文件句柄不可读")

    warn_section = (
        "\n**权限限制**\n" + "\n".join(f"- {w}" for w in warnings)
        if warnings else ""
    )

    output = (
        f"## System Snapshot  {time.strftime('%H:%M:%S')}\n\n"
        f"**Uptime / Load**\n```\n{data['uptime']}\nloadavg: {data['loadavg']}\n```\n\n"
        f"**Memory / Swap**\n```\n{data['mem']}\n```\n\n"
        f"**Disk (>80%)**\n```\n{data['disk_warn'] or '无高占用挂载点'}\n```\n\n"
        f"**Top 5 Processes (by CPU)**\n```\n{data['top_procs']}\n```"
        f"{warn_section}"
    )

    if mode in ("detail", "full"):
        rate = await _sample_rate(None)
        _, listen_out, _ = await _run("ss -lntp 2>/dev/null | head -n 20")
        output += f"\n\n**网络速率**\n```\n{rate}\n```\n\n**监听端口**\n```\n{listen_out}\n```"

    if mode == "full":
        _, df_full, _ = await _run("df -h | grep -v tmpfs")
        output += f"\n\n**全量磁盘**\n```\n{df_full}\n```"

    return _result("get_system_snapshot", t0, True, output)
