"""
perception/filter.py — 感知层

职责：持有基线，做 diff，只输出异常项。
核心设计：
  - 绝对阈值检测（内存/磁盘/负载/日志）不依赖基线，直接判断
  - 变化检测（进程/网络接口）依赖基线对比
  - fingerprint + 冷却时间：同一告警按级别限频，不每轮重复推给 LLM
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Literal

from .collector import RawSnapshot


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

AlertLevel = Literal["CRITICAL", "HIGH", "INFO"]

# 各级别冷却时间（秒）：同一 fingerprint 在冷却期内不重复推给 LLM
_COOLDOWN: dict[str, float] = {
    "CRITICAL": 30.0,
    "HIGH":     120.0,
    "INFO":     600.0,
}


@dataclass
class PerceptionAlert:
    """单条异常告警"""
    level:           AlertLevel
    category:        str           # memory / disk / disk_inode / load / process / log / network
    message:         str           # 一行人类可读摘要（直接注入 LLM prompt）
    detail:          dict          # 原始数据，供工具按需展开
    suggested_tools: list[str] = field(default_factory=list)
    fingerprint:     str       = ""   # 去重 key，由 _make_fp() 生成


@dataclass
class PerceptionResult:
    """感知层输出，只含本轮需要推给 LLM 的告警"""
    timestamp:      float
    alerts:         list[PerceptionAlert]   # 已经过冷却过滤
    all_alerts:     list[PerceptionAlert]   # 本轮检测到的全部告警（含冷却中的）
    has_change:     bool
    baseline_age_s: float


# ---------------------------------------------------------------------------
# PerceptionFilter
# ---------------------------------------------------------------------------

class PerceptionFilter:
    """
    两类检测路径：
      _check_absolute()  内存/磁盘/负载/日志 → 绝对阈值，不依赖基线
      _check_stateful()  进程/网络接口 → 基线对比，变化才告警

    基线更新规则：
      - 有 HIGH/CRITICAL 变化告警 → 冻结基线（保持对比点）
      - TTL 到期且无活跃告警 → 安全重置
      - 无告警 → 正常滚动更新
    """

    BASELINE_TTL   = 300      # 基线有效期 5 分钟（无活跃告警时才重置）
    CPU_COUNT      = os.cpu_count() or 4

    # 绝对阈值
    MEM_CRITICAL_PCT  = 10.0
    MEM_HIGH_PCT      = 20.0
    DISK_CRITICAL_PCT = 95.0
    DISK_HIGH_PCT     = 85.0
    INODE_HIGH_PCT    = 80.0
    LOAD_HIGH_FACTOR  = 2.0
    LOAD_CRIT_FACTOR  = 4.0
    PROC_MEM_HIGH_PCT = 20.0

    def __init__(self) -> None:
        self._baseline:     RawSnapshot | None = None
        self._baseline_ts:  float = 0.0
        # fingerprint → 上次推送时间戳
        self._last_alerted: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def process(self, snapshot: RawSnapshot) -> PerceptionResult:
        now = time.monotonic()

        # 首次：建立基线，本轮不报告变化
        if self._baseline is None:
            self._baseline    = snapshot
            self._baseline_ts = now
            return PerceptionResult(
                timestamp=snapshot.timestamp,
                alerts=[],
                all_alerts=[],
                has_change=False,
                baseline_age_s=0.0,
            )

        # 两类检测
        all_alerts: list[PerceptionAlert] = []
        all_alerts += self._check_absolute(snapshot)
        all_alerts += self._check_stateful(snapshot, self._baseline)

        # 按级别排序
        _order = {"CRITICAL": 0, "HIGH": 1, "INFO": 2}
        all_alerts.sort(key=lambda a: _order[a.level])

        # 冷却过滤：只保留超过冷却时间的告警推给 LLM
        to_push: list[PerceptionAlert] = []
        for alert in all_alerts:
            fp = alert.fingerprint
            last = self._last_alerted.get(fp, 0.0)
            cooldown = _COOLDOWN.get(alert.level, 120.0)
            if (now - last) >= cooldown:
                to_push.append(alert)
                self._last_alerted[fp] = now

        # 基线更新（只作用于变化检测，绝对阈值检测不依赖基线）
        has_stateful_alert = any(
            a.category in ("process", "network") and a.level in ("CRITICAL", "HIGH")
            for a in all_alerts
        )
        if has_stateful_alert:
            # 冻结基线，重置 TTL 计时防止到期重置
            self._baseline_ts = now
        elif (now - self._baseline_ts) > self.BASELINE_TTL:
            # TTL 到期且无活跃变化告警：安全重置
            self._baseline    = snapshot
            self._baseline_ts = now
        else:
            # 正常滚动更新
            self._baseline = snapshot

        return PerceptionResult(
            timestamp=snapshot.timestamp,
            alerts=to_push,
            all_alerts=all_alerts,
            has_change=len(to_push) > 0,
            baseline_age_s=now - self._baseline_ts,
        )

    def reset_baseline(self) -> None:
        """工具执行后强制重建基线（让下次感知重新对比）"""
        self._baseline = None

    def acknowledge(self, fingerprint: str) -> None:
        """
        LLM 或用户确认某条告警后调用，将其冷却时间延长到 10 分钟。
        用于"我知道这个进程吃内存，不用再提醒"的场景。
        """
        self._last_alerted[fingerprint] = time.monotonic() + 600.0 - _COOLDOWN.get("HIGH", 120.0)

    # ------------------------------------------------------------------
    # 绝对阈值检测（不依赖基线）
    # ------------------------------------------------------------------

    def _check_absolute(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        alerts: list[PerceptionAlert] = []
        alerts += self._check_memory(snap)
        alerts += self._check_disk(snap)
        alerts += self._check_load(snap)
        alerts += self._check_logs(snap)
        return alerts

    def _check_memory(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        if snap.mem_total_b == 0:
            return []
        avail_pct = snap.mem_avail_b / snap.mem_total_b * 100
        avail_gb  = snap.mem_avail_b / (1024 ** 3)
        total_gb  = snap.mem_total_b / (1024 ** 3)
        alerts    = []

        if avail_pct < self.MEM_CRITICAL_PCT:
            msg = f"内存可用 {avail_pct:.1f}%（{avail_gb:.1f}GB / {total_gb:.1f}GB）"
            alerts.append(PerceptionAlert(
                level="CRITICAL", category="memory", message=msg,
                detail={"avail_pct": avail_pct, "avail_gb": avail_gb, "total_gb": total_gb},
                suggested_tools=["get_process_detail", "get_system_snapshot"],
                fingerprint=_make_fp("memory", "critical"),
            ))
        elif avail_pct < self.MEM_HIGH_PCT:
            msg = f"内存可用 {avail_pct:.1f}%（{avail_gb:.1f}GB / {total_gb:.1f}GB）"
            alerts.append(PerceptionAlert(
                level="HIGH", category="memory", message=msg,
                detail={"avail_pct": avail_pct, "avail_gb": avail_gb, "total_gb": total_gb},
                suggested_tools=["get_process_detail"],
                fingerprint=_make_fp("memory", "high"),
            ))

        if snap.swap_total_b > 0:
            swap_pct = snap.swap_used_b / snap.swap_total_b * 100
            if swap_pct > 50:
                alerts.append(PerceptionAlert(
                    level="HIGH", category="memory",
                    message=f"Swap 使用率 {swap_pct:.1f}%，系统可能存在内存压力",
                    detail={"swap_pct": swap_pct},
                    suggested_tools=["get_process_detail"],
                    fingerprint=_make_fp("memory", "swap"),
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
                    level="CRITICAL", category="disk",
                    message=f"磁盘 {d.mount} 使用率 {used_pct:.1f}%（剩余 {avail_gb:.1f}GB）",
                    detail={"mount": d.mount, "used_pct": used_pct, "avail_gb": avail_gb},
                    suggested_tools=["get_disk_detail"],
                    fingerprint=_make_fp("disk", d.mount),
                ))
            elif used_pct >= self.DISK_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH", category="disk",
                    message=f"磁盘 {d.mount} 使用率 {used_pct:.1f}%（剩余 {avail_gb:.1f}GB）",
                    detail={"mount": d.mount, "used_pct": used_pct, "avail_gb": avail_gb},
                    suggested_tools=["get_disk_detail"],
                    fingerprint=_make_fp("disk", d.mount),
                ))

            if d.inodes_total > 0:
                inode_pct = d.inodes_used / d.inodes_total * 100
                if inode_pct >= self.INODE_HIGH_PCT:
                    alerts.append(PerceptionAlert(
                        level="HIGH", category="disk_inode",
                        message=f"磁盘 {d.mount} inode 使用率 {inode_pct:.1f}%，可能导致无法创建新文件",
                        detail={"mount": d.mount, "inode_pct": inode_pct},
                        suggested_tools=["get_disk_detail"],
                        fingerprint=_make_fp("disk_inode", d.mount),
                    ))
        return alerts

    def _check_load(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        load1  = snap.load_avg[0]
        alerts = []
        if load1 > self.CPU_COUNT * self.LOAD_CRIT_FACTOR:
            alerts.append(PerceptionAlert(
                level="CRITICAL", category="load",
                message=f"系统负载 {load1:.1f}（{self.CPU_COUNT} 核，超过 {self.LOAD_CRIT_FACTOR}x）",
                detail={"load_avg": list(snap.load_avg), "cpu_count": self.CPU_COUNT},
                suggested_tools=["get_process_detail", "get_system_snapshot"],
                fingerprint=_make_fp("load", "critical"),
            ))
        elif load1 > self.CPU_COUNT * self.LOAD_HIGH_FACTOR:
            alerts.append(PerceptionAlert(
                level="HIGH", category="load",
                message=f"系统负载 {load1:.1f}（{self.CPU_COUNT} 核，超过 {self.LOAD_HIGH_FACTOR}x）",
                detail={"load_avg": list(snap.load_avg), "cpu_count": self.CPU_COUNT},
                suggested_tools=["get_process_detail"],
                fingerprint=_make_fp("load", "high"),
            ))
        return alerts

    def _check_logs(self, snap: RawSnapshot) -> list[PerceptionAlert]:
        if not snap.log_errors:
            return []
        error_lines = [
            l for l in snap.log_errors
            if any(kw in l.lower() for kw in ("error", "critical", "fatal", "oom", "killed"))
        ]
        if not error_lines:
            return []
        return [PerceptionAlert(
            level="HIGH", category="log",
            message=f"最近 5 分钟有 {len(error_lines)} 条错误日志",
            detail={"count": len(error_lines), "sample": error_lines[:3]},
            suggested_tools=["get_logs"],
            fingerprint=_make_fp("log", "errors"),
        )]

    # ------------------------------------------------------------------
    # 变化检测（依赖基线）
    # ------------------------------------------------------------------

    def _check_stateful(
        self, snap: RawSnapshot, baseline: RawSnapshot
    ) -> list[PerceptionAlert]:
        alerts: list[PerceptionAlert] = []
        alerts += self._check_processes(snap, baseline)
        alerts += self._check_networks(snap, baseline)
        return alerts

    def _check_processes(
        self, snap: RawSnapshot, baseline: RawSnapshot
    ) -> list[PerceptionAlert]:
        alerts = []
        baseline_pids = {p.pid for p in baseline.processes}
        current_pids  = {p.pid for p in snap.processes}

        # 新增高内存进程
        for p in snap.processes:
            if p.pid not in baseline_pids and p.mem_pct > self.PROC_MEM_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH", category="process",
                    message=f"进程 {p.name}（PID {p.pid}）内存占用 {p.mem_pct:.1f}%，为新增进程",
                    detail={"pid": p.pid, "name": p.name, "mem_pct": p.mem_pct, "cpu_pct": p.cpu_pct},
                    suggested_tools=["get_process_detail"],
                    fingerprint=_make_fp("process_new", str(p.pid)),
                ))

        # 高内存进程消失
        for p in baseline.processes:
            if p.pid not in current_pids and p.mem_pct > self.PROC_MEM_HIGH_PCT:
                alerts.append(PerceptionAlert(
                    level="HIGH", category="process",
                    message=f"进程 {p.name}（PID {p.pid}）已消失，原内存占用 {p.mem_pct:.1f}%",
                    detail={"pid": p.pid, "name": p.name, "mem_pct": p.mem_pct},
                    suggested_tools=["get_process_detail"],
                    fingerprint=_make_fp("process_gone", str(p.pid)),
                ))
        return alerts

    def _check_networks(
        self, snap: RawSnapshot, baseline: RawSnapshot
    ) -> list[PerceptionAlert]:
        alerts = []
        baseline_status = {n.interface: n.status for n in baseline.networks}

        for n in snap.networks:
            prev_status = baseline_status.get(n.interface)

            # 接口变为 DOWN（或首次发现就是 DOWN）
            if n.status == "DOWN" and prev_status != "DOWN":
                alerts.append(PerceptionAlert(
                    level="HIGH", category="network",
                    message=f"网络接口 {n.interface} 状态变为 DOWN",
                    detail={"interface": n.interface},
                    suggested_tools=["get_network_detail"],
                    fingerprint=_make_fp("network_down", n.interface),
                ))
            # 持续 DOWN（基线里也是 DOWN）→ 降为 INFO，不打断 LLM
            elif n.status == "DOWN" and prev_status == "DOWN":
                alerts.append(PerceptionAlert(
                    level="INFO", category="network",
                    message=f"网络接口 {n.interface} 持续 DOWN",
                    detail={"interface": n.interface},
                    suggested_tools=["get_network_detail"],
                    fingerprint=_make_fp("network_down_persist", n.interface),
                ))

            # 错误包（新增或显著增加）
            if n.rx_errors > 100 or n.tx_errors > 100 or n.rx_drops > 100:
                alerts.append(PerceptionAlert(
                    level="HIGH", category="network",
                    message=(
                        f"接口 {n.interface} 存在错误包："
                        f"rx_errors={n.rx_errors} tx_errors={n.tx_errors} "
                        f"rx_drops={n.rx_drops}"
                    ),
                    detail={
                        "interface": n.interface,
                        "rx_errors": n.rx_errors, "tx_errors": n.tx_errors,
                        "rx_drops":  n.rx_drops,
                    },
                    suggested_tools=["get_network_detail"],
                    fingerprint=_make_fp("network_errors", n.interface),
                ))
        return alerts


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _make_fp(*parts: str) -> str:
    """生成告警 fingerprint（category + key 的短 hash）"""
    raw = ":".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()[:12]
