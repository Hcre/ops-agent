"""
perception/filter.py — 感知层

职责：持有基线，做 diff，只输出异常项。
输出 PerceptionResult，包含 PerceptionAlert 列表，每条告警带 suggested_tools。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Literal

from .collector import RawSnapshot


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

AlertLevel = Literal["CRITICAL", "HIGH", "INFO"]


@dataclass
class PerceptionAlert:
    """单条异常告警"""
    level: AlertLevel
    category: str          # memory / disk / disk_inode / load / process / log / network
    message: str           # 一行人类可读摘要（直接注入 LLM prompt）
    detail: dict           # 原始数据，供工具按需展开
    suggested_tools: list[str] = field(default_factory=list)


@dataclass
class PerceptionResult:
    """感知层输出，只含异常项"""
    timestamp: float
    alerts: list[PerceptionAlert]
    has_change: bool
    baseline_age_s: float


# ---------------------------------------------------------------------------
# PerceptionFilter
# ---------------------------------------------------------------------------

class PerceptionFilter:
    """基线对比 + 异常检测，输出 PerceptionResult"""

    BASELINE_TTL = 300      # 基线有效期 5 分钟
    CPU_COUNT = os.cpu_count() or 4

    # 阈值
    MEM_CRITICAL_PCT  = 10.0
    MEM_HIGH_PCT      = 20.0
    DISK_CRITICAL_PCT = 95.0
    DISK_HIGH_PCT     = 85.0
    INODE_HIGH_PCT    = 80.0
    LOAD_HIGH_FACTOR  = 2.0    # load > cpu_count * 2
    LOAD_CRIT_FACTOR  = 4.0
    PROC_MEM_HIGH_PCT = 20.0   # 单进程内存占比

    def __init__(self) -> None:
        self._baseline: RawSnapshot | None = None
        self._baseline_ts: float = 0.0

    def process(self, snapshot: RawSnapshot) -> PerceptionResult:
        now = time.monotonic()

        # 首次或基线过期：重建基线，本轮不报告变化
        if self._baseline is None or (now - self._baseline_ts) > self.BASELINE_TTL:
            self._baseline = snapshot
            self._baseline_ts = now
            return PerceptionResult(
                timestamp=snapshot.timestamp,
                alerts=[],
                has_change=False,
                baseline_age_s=0.0,
            )

        alerts: list[PerceptionAlert] = []
        alerts += self._check_memory(snapshot)
        alerts += self._check_disk(snapshot)
        alerts += self._check_load(snapshot)
        alerts += self._check_processes(snapshot, self._baseline)
        alerts += self._check_networks(snapshot)
        alerts += self._check_logs(snapshot)

        # 按级别排序
        order = {"CRITICAL": 0, "HIGH": 1, "INFO": 2}
        alerts.sort(key=lambda a: order[a.level])

        # 无 CRITICAL 时滚动更新基线
        if not any(a.level == "CRITICAL" for a in alerts):
            self._baseline = snapshot
            self._baseline_ts = now

        return PerceptionResult(
            timestamp=snapshot.timestamp,
            alerts=alerts,
            has_change=len(alerts) > 0,
            baseline_age_s=now - self._baseline_ts,
        )

    def reset_baseline(self) -> None:
        """工具执行后强制重建基线"""
        self._baseline = None

    # ------------------------------------------------------------------
    # 各项检测
    # ------------------------------------------------------------------

    def _check_memory(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        if snap.mem_total_b == 0:
            return []
        avail_pct = snap.mem_avail_b / snap.mem_total_b * 100
        avail_gb  = snap.mem_avail_b / (1024 ** 3)
        total_gb  = snap.mem_total_b / (1024 ** 3)

        alerts = []
        if avail_pct < self.MEM_CRITICAL_PCT:
            alerts.append(PerceptionAlert(
                level="CRITICAL",
                category="memory",
                message=f"内存可用 {avail_pct:.1f}%（{avail_gb:.1f}GB / {total_gb:.1f}GB）",
                detail={"avail_pct": avail_pct, "avail_gb": avail_gb, "total_gb": total_gb},
                suggested_tools=["get_process_detail", "get_system_snapshot"],
            ))
        elif avail_pct < self.MEM_HIGH_PCT:
            alerts.append(PerceptionAlert(
                level="HIGH",
                category="memory",
                message=f"内存可用 {avail_pct:.1f}%（{avail_gb:.1f}GB / {total_gb:.1f}GB）",
                detail={"avail_pct": avail_pct, "avail_gb": avail_gb, "total_gb": total_gb},
                suggested_tools=["get_process_detail"],
            ))

        # Swap 使用率
        if snap.swap_total_b > 0:
            swap_pct = snap.swap_used_b / snap.swap_total_b * 100
            if swap_pct > 50:
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="memory",
                    message=f"Swap 使用率 {swap_pct:.1f}%，系统可能存在内存压力",
                    detail={"swap_pct": swap_pct},
                    suggested_tools=["get_process_detail"],
                ))
        return alerts

    def _check_disk(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        alerts = []
        for d in snap.disks:
            if d.total_b == 0:
                continue
            used_pct = d.used_b / d.total_b * 100
            avail_gb = d.avail_b / (1024 ** 3)

            if used_pct >= self.DISK_CRITICAL_PCT:
                alerts.append(PerceptionAlert(
                    level="CRITICAL",
                    category="disk",
                    message=f"磁盘 {d.mount} 使用率 {used_pct:.1f}%（剩余 {avail_gb:.1f}GB）",
                    detail={"mount": d.mount, "used_pct": used_pct, "avail_gb": avail_gb},
                    suggested_tools=["get_disk_detail"],
                ))
            elif used_pct >= self.DISK_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="disk",
                    message=f"磁盘 {d.mount} 使用率 {used_pct:.1f}%（剩余 {avail_gb:.1f}GB）",
                    detail={"mount": d.mount, "used_pct": used_pct, "avail_gb": avail_gb},
                    suggested_tools=["get_disk_detail"],
                ))

            # Inode 检测
            if d.inodes_total > 0:
                inode_pct = d.inodes_used / d.inodes_total * 100
                if inode_pct >= self.INODE_HIGH_PCT:
                    alerts.append(PerceptionAlert(
                        level="HIGH",
                        category="disk_inode",
                        message=f"磁盘 {d.mount} inode 使用率 {inode_pct:.1f}%，可能导致无法创建新文件",
                        detail={"mount": d.mount, "inode_pct": inode_pct},
                        suggested_tools=["get_disk_detail"],
                    ))
        return alerts

    def _check_load(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        load1 = snap.load_avg[0]
        alerts = []
        if load1 > self.CPU_COUNT * self.LOAD_CRIT_FACTOR:
            alerts.append(PerceptionAlert(
                level="CRITICAL",
                category="load",
                message=f"系统负载 {load1:.1f}（{self.CPU_COUNT} 核，超过 {self.LOAD_CRIT_FACTOR}x）",
                detail={"load_avg": list(snap.load_avg), "cpu_count": self.CPU_COUNT},
                suggested_tools=["get_process_detail", "get_system_snapshot"],
            ))
        elif load1 > self.CPU_COUNT * self.LOAD_HIGH_FACTOR:
            alerts.append(PerceptionAlert(
                level="HIGH",
                category="load",
                message=f"系统负载 {load1:.1f}（{self.CPU_COUNT} 核，超过 {self.LOAD_HIGH_FACTOR}x）",
                detail={"load_avg": list(snap.load_avg), "cpu_count": self.CPU_COUNT},
                suggested_tools=["get_process_detail"],
            ))
        return alerts

    def _check_processes(
        self, snap: RawSnapshot, baseline: RawSnapshot
    ) -> list[PerceptionAlert]:
        alerts = []
        baseline_pids = {p.pid for p in baseline.processes}
        baseline_names = {p.name for p in baseline.processes}

        for p in snap.processes:
            # 新增高内存进程
            if p.pid not in baseline_pids and p.mem_pct > self.PROC_MEM_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="process",
                    message=f"进程 {p.name}（PID {p.pid}）内存占用 {p.mem_pct:.1f}%，为新增进程",
                    detail={"pid": p.pid, "name": p.name, "mem_pct": p.mem_pct, "cpu_pct": p.cpu_pct},
                    suggested_tools=["get_process_detail"],
                ))

        # 进程消失（基线有、现在没有）
        current_pids = {p.pid for p in snap.processes}
        for p in baseline.processes:
            if p.pid not in current_pids and p.mem_pct > self.PROC_MEM_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="process",
                    message=f"进程 {p.name}（PID {p.pid}）已消失，原内存占用 {p.mem_pct:.1f}%",
                    detail={"pid": p.pid, "name": p.name, "mem_pct": p.mem_pct},
                    suggested_tools=["get_process_detail"],
                ))
        return alerts

    def _check_networks(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        alerts = []
        for n in snap.networks:
            if n.status == "DOWN":
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="network",
                    message=f"网络接口 {n.interface} 状态 DOWN",
                    detail={"interface": n.interface},
                    suggested_tools=["get_network_detail"],
                ))
            elif n.rx_errors > 100 or n.tx_errors > 100 or n.rx_drops > 100:
                alerts.append(PerceptionAlert(
                    level="HIGH",
                    category="network",
                    message=(
                        f"接口 {n.interface} 存在错误包："
                        f"rx_errors={n.rx_errors} tx_errors={n.tx_errors} "
                        f"rx_drops={n.rx_drops} tx_drops={n.tx_drops}"
                    ),
                    detail={
                        "interface": n.interface,
                        "rx_errors": n.rx_errors, "tx_errors": n.tx_errors,
                        "rx_drops": n.rx_drops,   "tx_drops": n.tx_drops,
                    },
                    suggested_tools=["get_network_detail"],
                ))
        return alerts

    def _check_logs(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        if not snap.log_errors:
            return []
        # 只在有错误日志时报告
        error_lines = [
            l for l in snap.log_errors
            if any(kw in l.lower() for kw in ("error", "critical", "fatal", "oom", "killed"))
        ]
        if not error_lines:
            return []
        sample = error_lines[:3]
        return [PerceptionAlert(
            level="HIGH",
            category="log",
            message=f"最近 5 分钟有 {len(error_lines)} 条错误日志",
            detail={"count": len(error_lines), "sample": sample},
            suggested_tools=["get_logs"],
        )]
