"""
tests/week2_integration_test.py — Week 2 集成测试

测试：
1. PermissionManager 4 步决策管道
2. TaskManager 6 状态 FSM
3. PerceptionAggregator 系统感知
4. ErrorRecovery 错误恢复策略
"""
import asyncio
import pytest
import time
from config import AgentConfig
from security.permission_manager import PermissionManager
from managers.task_manager import TaskManager
from perception.aggregator import PerceptionAggregator
from core.error_recovery import ErrorRecovery, RecoveryStrategy


class TestPermissionManager:
    """PermissionManager 测试"""

    def test_deny_blacklist(self):
        """测试绝对黑名单拒绝"""
        config = AgentConfig(mode="default")
        pm = PermissionManager(config)

        decision = pm.check("exec_bash", {"cmd": "rm -rf /"})
        assert decision.behavior == "deny"
        assert decision.risk_level == "CRITICAL"

    def test_allow_readonly(self):
        """测试只读命令自动放行"""
        config = AgentConfig(mode="default")
        pm = PermissionManager(config)

        decision = pm.check("exec_bash", {"cmd": "ls -la /tmp"})
        assert decision.behavior == "allow"
        assert decision.risk_level == "LOW"

    def test_plan_mode_rejects_write(self):
        """测试 plan 模式拒绝写操作"""
        config = AgentConfig(mode="plan")
        pm = PermissionManager(config)

        decision = pm.check("exec_bash", {"cmd": "rm /tmp/test.txt"})
        assert decision.behavior == "deny"
        assert "Plan mode" in decision.reason

    def test_auto_mode_allows_medium_risk(self):
        """测试 auto 模式自动放行中等风险操作"""
        config = AgentConfig(mode="auto")
        pm = PermissionManager(config)

        decision = pm.check("exec_bash", {"cmd": "mv /tmp/a /tmp/b"})
        assert decision.behavior == "allow"
        assert decision.risk_level == "MEDIUM"

    def test_ask_for_high_risk(self):
        """测试高风险操作询问用户"""
        config = AgentConfig(mode="default")
        pm = PermissionManager(config)

        decision = pm.check("exec_bash", {"cmd": "rm -rf /home/user"})
        assert decision.behavior == "ask"
        assert decision.risk_level == "HIGH"


class TestTaskManager:
    """TaskManager 测试"""

    def test_create_task(self):
        """测试创建任务"""
        config = AgentConfig()
        tm = TaskManager(config)

        task = tm.create_task("Test task", risk_level="MEDIUM")
        assert task.status == "pending"
        assert task.title == "Test task"
        assert task.risk_level == "MEDIUM"

    def test_state_transitions(self):
        """测试状态转移"""
        config = AgentConfig()
        tm = TaskManager(config)

        task = tm.create_task("Test task")
        assert task.status == "pending"

        # pending → running
        task = tm.transition(task.id, "running", op_id="op123")
        assert task.status == "running"
        assert task.op_id == "op123"

        # running → success
        task = tm.transition(task.id, "success")
        assert task.status == "success"

    def test_invalid_transition(self):
        """测试无效的状态转移"""
        config = AgentConfig()
        tm = TaskManager(config)

        task = tm.create_task("Test task")
        with pytest.raises(ValueError):
            tm.transition(task.id, "success")  # pending 不能直接转移到 success

    def test_blocking_tasks(self):
        """测试任务阻塞"""
        config = AgentConfig()
        tm = TaskManager(config)

        task1 = tm.create_task("Task 1")
        task2 = tm.create_task("Task 2")

        # Task 2 被 Task 1 阻塞
        tm.transition(task2.id, "blocked", blocked_by=[task1.id])
        assert task2.status == "blocked"

        # Task 1 完成，解除 Task 2 的阻塞
        tm.transition(task1.id, "success")
        unblocked = tm.unblock_tasks(task1.id)
        assert len(unblocked) == 1
        assert unblocked[0].id == task2.id

    def test_list_tasks_by_status(self):
        """测试按状态列出任务"""
        config = AgentConfig()
        tm = TaskManager(config)

        tm.create_task("Task 1")
        tm.create_task("Task 2")
        task3 = tm.create_task("Task 3")

        tm.transition(task3.id, "running")

        pending = tm.list_tasks(status="pending")
        running = tm.list_tasks(status="running")

        assert len(pending) == 2
        assert len(running) == 1


class TestPerceptionAggregator:
    """PerceptionAggregator 测试"""

    @pytest.mark.asyncio
    async def test_snapshot(self):
        """测试系统快照"""
        config = AgentConfig()
        pa = PerceptionAggregator(config)

        snapshot = await pa.snapshot()
        assert snapshot.timestamp > 0
        assert isinstance(snapshot.disk, list)
        assert isinstance(snapshot.processes, list)
        assert isinstance(snapshot.network, list)
        assert isinstance(snapshot.load_average, tuple)
        assert len(snapshot.load_average) == 3

    @pytest.mark.asyncio
    async def test_snapshot_to_dict(self):
        """测试快照转换为字典"""
        config = AgentConfig()
        pa = PerceptionAggregator(config)

        snapshot = await pa.snapshot()
        data = pa.to_dict(snapshot)

        assert "timestamp" in data
        assert "disk" in data
        assert "processes" in data
        assert "network" in data
        assert "load_average" in data
        assert "memory_available_gb" in data


class TestErrorRecovery:
    """ErrorRecovery 测试"""

    @pytest.mark.asyncio
    async def test_backoff_retry_success(self):
        """测试指数退避重试成功"""
        config = AgentConfig()
        er = ErrorRecovery(config)

        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary error")
            return "success"

        result = await er.backoff_retry(failing_fn)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_retry_failure(self):
        """测试指数退避重试失败"""
        config = AgentConfig(max_recovery_attempts=2)
        er = ErrorRecovery(config)

        async def always_failing():
            raise ValueError("Persistent error")

        with pytest.raises(ValueError):
            await er.backoff_retry(always_failing)

        attempts = er.get_attempts()
        assert len(attempts) == 1  # 最后一次不记录

    @pytest.mark.asyncio
    async def test_compact_retry(self):
        """测试上下文压缩重试"""
        config = AgentConfig()
        er = ErrorRecovery(config)

        call_count = 0
        compact_called = False

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Context overflow")
            return "success"

        def compact_fn():
            nonlocal compact_called
            compact_called = True

        result = await er.compact_retry(failing_fn, compact_fn)
        assert result == "success"
        assert compact_called

    def test_fallback_to_readonly(self):
        """测试降级到只读模式"""
        config = AgentConfig()
        er = ErrorRecovery(config)

        er.fallback_to_readonly()
        attempts = er.get_attempts()
        assert len(attempts) == 1
        assert attempts[0].strategy == RecoveryStrategy.FALLBACK

    def test_should_fallback(self):
        """测试是否应该降级"""
        config = AgentConfig(max_recovery_attempts=3)
        er = ErrorRecovery(config)

        # 添加 3 次失败
        for _ in range(3):
            er._attempts.append(
                er._attempts.__class__(
                    strategy=RecoveryStrategy.BACKOFF,
                    attempt_num=1,
                    delay_ms=100,
                    success=False,
                    error="Test error",
                )
            )

        assert er.should_fallback()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
