"""
managers/state_store.py — SQLite 状态持久化层

对应 Week 2 M4：为 task_manager 和 circuit_breaker 提供持久化后端
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Literal


@dataclass
class TaskRow:
    """任务表行"""
    id: str
    title: str
    status: str
    risk_level: str | None
    op_id: str | None
    blocked_by: list[str]
    created_at: float
    updated_at: float


@dataclass
class CircuitRow:
    """熔断状态表行"""
    module: str
    state: Literal["CLOSED", "HALF_OPEN", "OPEN"]
    fail_count: int
    frozen_until: float | None


class StateStore:
    """SQLite 状态持久化层"""

    def __init__(self, db_path: str = "ops_agent.db") -> None:
        self._db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化数据库 schema"""
        with self._conn() as conn:
            # 任务表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT    PRIMARY KEY,
                    title       TEXT    NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'pending',
                    risk_level  TEXT,
                    op_id       TEXT,
                    blocked_by  TEXT,
                    created_at  REAL    NOT NULL,
                    updated_at  REAL    NOT NULL
                )
            """)

            # 熔断状态表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS circuit_state (
                    module       TEXT    PRIMARY KEY,
                    state        TEXT    NOT NULL DEFAULT 'CLOSED',
                    fail_count   INTEGER NOT NULL DEFAULT 0,
                    frozen_until REAL    DEFAULT NULL
                )
            """)

            # 操作审计索引表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS op_index (
                    op_id      TEXT    PRIMARY KEY,
                    phase      TEXT    NOT NULL,
                    summary    TEXT,
                    ts         REAL    NOT NULL
                )
            """)

            # 快照元数据表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    op_id      TEXT    PRIMARY KEY,
                    snap_path  TEXT    NOT NULL,
                    created_at REAL    NOT NULL,
                    expires_at REAL    NOT NULL
                )
            """)

            conn.commit()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """带 WAL 模式的连接上下文管理器"""
        conn = sqlite3.connect(self._db_path)
        try:
            # 启用 WAL 模式以支持并发读写
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    # ── Tasks ──────────────────────────────────────

    def upsert_task(self, task: TaskRow) -> None:
        """
        插入或更新任务

        Args:
            task: TaskRow 对象
        """
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO tasks
                    (id, title, status, risk_level, op_id, blocked_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status     = excluded.status,
                    risk_level = excluded.risk_level,
                    op_id      = excluded.op_id,
                    blocked_by = excluded.blocked_by,
                    updated_at = excluded.updated_at
            """, (
                task.id,
                task.title,
                task.status,
                task.risk_level,
                task.op_id,
                json.dumps(task.blocked_by),
                task.created_at,
                task.updated_at
            ))
            conn.commit()

    def load_all_tasks(self) -> list[TaskRow]:
        """
        加载所有任务

        Returns:
            TaskRow 列表
        """
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT id, title, status, risk_level, op_id, blocked_by, created_at, updated_at
                FROM tasks
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()

        tasks = []
        for row in rows:
            blocked_by = json.loads(row[5]) if row[5] else []
            tasks.append(TaskRow(
                id=row[0],
                title=row[1],
                status=row[2],
                risk_level=row[3],
                op_id=row[4],
                blocked_by=blocked_by,
                created_at=row[6],
                updated_at=row[7]
            ))
        return tasks

    def delete_task(self, task_id: str) -> None:
        """
        删除任务

        Args:
            task_id: 任务 ID
        """
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()

    # ── Circuit Breaker ────────────────────────────

    def get_circuit(self, module: str) -> CircuitRow:
        """
        获取熔断状态

        Args:
            module: 模块名称

        Returns:
            CircuitRow，不存在则返回默认 CLOSED 状态
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT module, state, fail_count, frozen_until FROM circuit_state WHERE module = ?",
                (module,)
            )
            row = cursor.fetchone()

        if row:
            return CircuitRow(
                module=row[0],
                state=row[1],  # type: ignore
                fail_count=row[2],
                frozen_until=row[3]
            )
        else:
            return CircuitRow(
                module=module,
                state="CLOSED",
                fail_count=0,
                frozen_until=None
            )

    def save_circuit(self, row: CircuitRow) -> None:
        """
        保存熔断状态

        Args:
            row: CircuitRow 对象
        """
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO circuit_state (module, state, fail_count, frozen_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(module) DO UPDATE SET
                    state        = excluded.state,
                    fail_count   = excluded.fail_count,
                    frozen_until = excluded.frozen_until
            """, (row.module, row.state, row.fail_count, row.frozen_until))
            conn.commit()

    # ── Snapshots ──────────────────────────────────

    def register_snapshot(self, op_id: str, snap_path: str, ttl: float = 86400) -> None:
        """
        注册快照

        Args:
            op_id: 操作 ID
            snap_path: 快照文件路径
            ttl: 生存时间（秒，默认 24 小时）
        """
        now = time.time()
        expires_at = now + ttl

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO snapshots (op_id, snap_path, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(op_id) DO UPDATE SET
                    snap_path  = excluded.snap_path,
                    expires_at = excluded.expires_at
            """, (op_id, snap_path, now, expires_at))
            conn.commit()

    def get_snapshot(self, op_id: str) -> str | None:
        """
        获取快照路径

        Args:
            op_id: 操作 ID

        Returns:
            快照路径，已过期或不存在返回 None
        """
        now = time.time()

        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT snap_path, expires_at FROM snapshots WHERE op_id = ?",
                (op_id,)
            )
            row = cursor.fetchone()

        if row and row[1] > now:
            return row[0]
        return None

    def purge_expired_snapshots(self) -> int:
        """
        清理过期快照

        Returns:
            清理的快照数量
        """
        now = time.time()

        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM snapshots WHERE expires_at <= ?",
                (now,)
            )
            conn.commit()
            return cursor.rowcount
