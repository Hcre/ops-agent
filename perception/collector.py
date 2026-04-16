"""
perception/collector.py — 采集层

职责：只负责拿原始数据，不做任何判断。
单项失败不影响其他项，所有异常降级为空值。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AgentConfig


# ---------------------------------------------------------------------------
# 原始数据结构
# ---------------------------------------------------------------------------

@dataclass
class DiskRaw:
    mount: str
    total_b: int
    used_b: int
    avail_b: int
    inodes_total: int = 0
    inodes_used: int = 0


@dataclass
class ProcessRaw:
    pid: int
    name: str
    cpu_pct: float
    mem_pct: float
    mem_rss_kb: int
    status: str
    elapsed: str   # etime 格式


@dataclass
class NetworkRaw:
    interface: str
    status: str          # UP / DOWN
    rx_bytes: int        # 累计，需两次采样才能算速率
    tx_bytes: int
    rx_errors: int
    tx_errors: int
    rx_drops: int
    tx_drops: int


@dataclass
class RawSnapshot:
    """采集层原始快照，不做任何过滤"""
    timestamp: float
    load_avg: tuple[float, float, float]
    mem_total_b: int
    mem_avail_b: int
    swap_total_b: int
    swap_used_b: int
    disks: list[DiskRaw]
    processes: list[ProcessRaw]       # top 15 by mem%
    networks: list[NetworkRaw]
    log_errors: list[str]             # 最近 30 条 warning+ 日志
    errors: dict[str, str]            # 采集失败的项 {item: error_msg}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class Collector:
    """原始数据采集器，并行采集，单项失败不崩溃"""

    def __init__(self, config: "AgentConfig") -> None:
        self._timeout = getattr(config, "perception_timeout_s", 5)

    async def collect(self) -> RawSnapshot:
        results = await asyncio.gather(
            self._collect_load_mem(),
            self._collect_disks(),
            self._collect_processes(),
            self._collect_networks(),
            self._collect_logs(),
            return_exceptions=True,
        )

        errors: dict[str, str] = {}
        keys = ["load_mem", "disks", "processes", "networks", "logs"]
        defaults = [
            ((0.0, 0.0, 0.0), 0, 0, 0, 0),
            [],
            [],
            [],
            [],
        ]
        data = []
        for key, result, default in zip(keys, results, defaults):
            if isinstance(result, Exception):
                errors[key] = str(result)
                data.append(default)
            else:
                data.append(result)

        load_mem, disks, processes, networks, logs = data
        load, mem_total, mem_avail, swap_total, swap_used = load_mem

        return RawSnapshot(
            timestamp=time.time(),
            load_avg=load,
            mem_total_b=mem_total,
            mem_avail_b=mem_avail,
            swap_total_b=swap_total,
            swap_used_b=swap_used,
            disks=disks,
            processes=processes,
            networks=networks,
            log_errors=logs,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # 各项采集
    # ------------------------------------------------------------------

    async def _collect_load_mem(self) -> tuple:
        load = (0.0, 0.0, 0.0)
        mem_total = mem_avail = swap_total = swap_used = 0

        ok, out = await self._run("cat /proc/loadavg")
        if ok:
            parts = out.split()
            if len(parts) >= 3:
                load = (float(parts[0]), float(parts[1]), float(parts[2]))

        ok, out = await self._run("cat /proc/meminfo")
        if ok:
            mem: dict[str, int] = {}
            for line in out.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    try:
                        mem[k.strip()] = int(v.strip().split()[0]) * 1024
                    except (ValueError, IndexError):
                        pass
            mem_total  = mem.get("MemTotal", 0)
            mem_avail  = mem.get("MemAvailable", 0)
            swap_total = mem.get("SwapTotal", 0)
            swap_used  = swap_total - mem.get("SwapFree", 0)

        return load, mem_total, mem_avail, swap_total, swap_used

    async def _collect_disks(self) -> list[DiskRaw]:
        disks: list[DiskRaw] = []

        ok, out = await self._run("df -B1 --output=source,size,used,avail,target 2>/dev/null | tail -n +2")
        if not ok:
            return disks
        mounts: dict[str, DiskRaw] = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                mount = parts[4]
                if any(mount.startswith(p) for p in ("/proc", "/sys", "/dev", "/run")):
                    continue
                d = DiskRaw(
                    mount=mount,
                    total_b=int(parts[1]),
                    used_b=int(parts[2]),
                    avail_b=int(parts[3]),
                )
                mounts[mount] = d
            except (ValueError, IndexError):
                pass

        # inode 信息
        ok2, out2 = await self._run("df -i --output=target,itotal,iused 2>/dev/null | tail -n +2")
        if ok2:
            for line in out2.splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount = parts[0]
                if mount in mounts:
                    try:
                        mounts[mount].inodes_total = int(parts[1])
                        mounts[mount].inodes_used  = int(parts[2])
                    except ValueError:
                        pass

        return list(mounts.values())

    async def _collect_processes(self) -> list[ProcessRaw]:
        ok, out = await self._run(
            "ps aux --sort=-%mem | head -n 16 | tail -n +2"
        )
        if not ok:
            return []
        procs = []
        for line in out.splitlines():
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            try:
                procs.append(ProcessRaw(
                    pid=int(parts[1]),
                    name=parts[10].split()[0],
                    cpu_pct=float(parts[2]),
                    mem_pct=float(parts[3]),
                    mem_rss_kb=int(parts[5]),
                    status=parts[7],
                    elapsed="",
                ))
            except (ValueError, IndexError):
                pass
        return procs

    async def _collect_networks(self) -> list[NetworkRaw]:
        ok, out = await self._run("cat /proc/net/dev")
        if not ok:
            return []

        # 接口状态
        _, ip_out = await self._run("ip link show 2>/dev/null")
        up_ifaces: set[str] = set()
        for line in ip_out.splitlines():
            if "state UP" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    up_ifaces.add(parts[1].strip().split("@")[0])

        nets = []
        for line in out.splitlines():
            if ":" not in line or "Inter" in line or "face" in line:
                continue
            iface, stats = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            try:
                f = stats.split()
                nets.append(NetworkRaw(
                    interface=iface,
                    status="UP" if iface in up_ifaces else "DOWN",
                    rx_bytes=int(f[0]),
                    tx_bytes=int(f[8]),
                    rx_errors=int(f[2]),
                    tx_errors=int(f[10]),
                    rx_drops=int(f[3]),
                    tx_drops=int(f[11]),
                ))
            except (ValueError, IndexError):
                pass
        return nets

    async def _collect_logs(self) -> list[str]:
        ok, out = await self._run(
            "journalctl -p warning --since '5 minutes ago' -n 30 --no-pager --output=short 2>/dev/null"
        )
        if not ok or not out.strip():
            return []
        return [l for l in out.splitlines() if l.strip()]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _run(self, cmd: str) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            return True, stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            return False, ""
