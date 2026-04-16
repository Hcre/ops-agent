"""
managers/task_manager.py — 任务管理器

对应 Week 2 M5：6 状态 FSM 管理任务生命周期
状态：pending → running → success / failed / blocked / cancelled
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from managers.state_store import StateStore, TaskRow

if TYPE_CHECKING:
    from config import AgentConfig

TaskStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]


@dataclass
class Task:
    """任务对象"""
    id: str
    title: str
    status: TaskStatus
    risk_level: str | None = None
    op_id: str | None = None
    blocked_by: list[str] | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_row(cls, row: TaskRow) -> Task:
        """从 TaskRow 转换"""
        return cls(
            id=row.id,
            title=row.title,
            status=row.status,  # type: ignore
            risk_level=row.risk_level,
            op_id=row.op_id,
            blocked_by=row.blocked_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def to_row(self) -> TaskRow:
        """转换为 TaskRow"""
        return TaskRow(
            id=self.id,
            title=self.title,
            status=self.status,
            risk_level=self.risk_level,
            op_id=self.op_id,
            blocked_by=self.blocked_by or [],
            created_at=self.created_at or time.time(),
            updated_at=self.updated_at or time.time(),
        )


class TaskManager:
    """任务管理器。

    6 状态 FSM：
    - pending:  初始状态，等待执行
    - running:  正在执行
    - success:  执行成功
    - failed:   执行失败
    - blocked:  被依赖任务阻塞
    - cancelled: 被取消
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._store = StateStore(config.db_path)
        self._tasks: dict[str, Task] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        """从持久化存储加载任务"""
        rows = self._store.load_all_tasks()
        for row in rows:
            task = Task.from_row(row)
            self._tasks[task.id] = task

    def create_task(self, title: str, risk_level: str | None = None) -> Task:
        """创建新任务。

        Args:
            title: 任务标题
            risk_level: 风险等级（LOW/MEDIUM/HIGH/CRITICAL）

        Returns:
            新创建的 Task 对象
        """
        now = time.time()
        task = Task(
            id=str(uuid.uuid4())[:8],
            title=title,
            status="pending",
            risk_level=risk_level,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.id] = task
        self._store.upsert_task(task.to_row())
        return task

    def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        op_id: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> Task:
        """状态转移。

        Args:
            task_id: 任务 ID
            new_status: 新状态
            op_id: 关联的操作 ID
            blocked_by: 阻塞该任务的任务 ID 列表

        Returns:
            更新后的 Task 对象

        Raises:
            ValueError: 如果任务不存在或状态转移无效
        """
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")

        task = self._tasks[task_id]

        # 验证状态转移有效性
        valid_transitions = {
            "pending": ["running", "blocked", "cancelled"],
            "running": ["success", "failed", "blocked"],
            "success": ["cancelled"],
            "failed": ["cancelled"],
            "blocked": ["pending", "cancelled"],
            "cancelled": [],
        }

        if new_status not in valid_transitions.get(task.status, []):
            raise ValueError(
                f"Invalid transition: {task.status} → {new_status}"
            )

        # 执行转移
        task.status = new_status
        task.updated_at = time.time()
        if op_id:
            task.op_id = op_id
        if blocked_by is not None:
            task.blocked_by = blocked_by

        self._store.upsert_task(task.to_row())
        return task

    def get_task(self, task_id: str) -> Task | None:
        """获取任务。

        Args:
            task_id: 任务 ID

        Returns:
            Task 对象，不存在返回 None
        """
        return self._tasks.get(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        """列出任务。

        Args:
            status: 按状态过滤（可选）

        Returns:
            Task 列表
        """
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at or 0, reverse=True)

    def delete_task(self, task_id: str) -> None:
        """删除任务。

        Args:
            task_id: 任务 ID
        """
        if task_id in self._tasks:
            del self._tasks[task_id]
        self._store.delete_task(task_id)

    def get_blocked_tasks(self, task_id: str) -> list[Task]:
        """获取被指定任务阻塞的所有任务。

        Args:
            task_id: 任务 ID

        Returns:
            被阻塞的 Task 列表
        """
        blocked = []
        for task in self._tasks.values():
            if task.blocked_by and task_id in task.blocked_by:
                blocked.append(task)
        return blocked

    def unblock_tasks(self, task_id: str) -> list[Task]:
        """解除被指定任务阻塞的所有任务。

        Args:
            task_id: 任务 ID

        Returns:
            被解除阻塞的 Task 列表
        """
        unblocked = []
        for task in self.get_blocked_tasks(task_id):
            if task.blocked_by:
                task.blocked_by.remove(task_id)
                if not task.blocked_by:
                    # 所有阻塞都解除，转移回 pending
                    self.transition(task.id, "pending")
                    unblocked.append(task)
                else:
                    # 仍有其他阻塞，保持 blocked 状态
                    self._store.upsert_task(task.to_row())
        return unblocked
