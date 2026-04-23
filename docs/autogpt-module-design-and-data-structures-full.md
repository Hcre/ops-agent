# 🧩 AutoGPT 模块设计与关键数据结构深度分析
## OpsAgent 可借鉴的具体方案

---

## 目录

1. [概述](#一概述)
2. [核心模块设计分析](#二核心模块设计分析)
3. [关键数据结构深度剖析](#三关键数据结构深度剖析)
4. [OpsAgent 可借鉴的设计模式](#四opsagent-可借鉴的设计模式)
5. [具体实现建议](#五具体实现建议)
6. [对比总结](#六对比总结)

---

## 一、概述

本文档深入分析 AutoGPT 的核心模块设计和关键数据结构，重点关注：
- 模块的职责划分和设计模式
- 关键数据结构的设计原理
- 数据流和状态管理机制
- 与 OpsAgent 的对比分析
- 具体的代码实现建议

---

## 二、核心模块设计分析

### 2.1 ExecutionProcessor（执行处理器）

#### 设计原理

**职责**:
- 管理所有 Graph Execution 的生命周期
- 协调前端、后端、执行引擎之间的交互
- 处理异步执行和状态同步

**设计模式**:
- **Strategy Pattern**: 不同类型使用不同执行策略
- **Observer Pattern**: 通过 EventBus 通知状态变化
- **Command Pattern**: 将操作封装为命令对象

**关键设计**:
```python
class ExecutionProcessor:
    """执行处理器 - AutoGPT 的核心执行引擎"""
    
    def __init__(
        self,
        execution_manager: ExecutionManager,
        event_bus: EventBus,
    ):
        self._execution_manager = execution_manager
        self._event_bus = event_bus
    
    async def on_graph_execution(
        self,
        graph_exec_entry: GraphExecEntry
    ):
        """处理 Graph Execution 事件"""
        
        # 1. 更新状态为 RUNNING
        await self._update_status(
            graph_exec_entry.id,
            GraphExecutionStatus.RUNNING
        )
        
        # 2. 发送开始事件
        await self._event_bus.emit(
            "graph_execution_started",
            {
                "graph_exec_id": graph_exec_entry.id,
                "graph_id": graph_exec_entry.graph_id,
                "timestamp": datetime.utcnow(),
            }
        )
        
        try:
            # 3. 执行 Graph
            result = await self._execute_graph(
                graph_exec_entry
            )
            
            # 4. 更新状态为 COMPLETED
            await self._update_status(
                graph_exec_entry.id,
                GraphExecutionStatus.COMPLETED
            )
            
            # 5. 发送完成事件
            await self._event_bus.emit(
                "graph_execution_completed",
                {
                    "graph_exec_id": graph_exec_entry.id,
                    "result": result,
                }
            )
            
            return result
        
        except Exception as e:
            # 6. 更新状态为 FAILED
            await self._update_status(
                graph_exec_entry.id,
                GraphExecutionStatus.FAILED
            )
            
            # 7. 发送失败事件
            await self._event_bus.emit(
                "graph_execution_failed",
                {
                    "graph_exec_id": graph_exec_entry.id,
                    "error": str(e),
                }
            )
            
            raise
```

#### OpsAgent 对应实现

**当前实现**:
```python
class AgentLoop:
    """Agent 循环执行器"""
    
    async def run(self):
        """简单的循环执行"""
        while turn_count < max_turns:
            perception = collect_snapshot()
            response = await llm.generate(messages + perception)
            
            for tool_call in response.tool_calls:
                result = await execute_tool(tool_call)
                messages.append(result)
            
            turn_count += 1
```

**问题分析**:
1. ❌ 缺少事件机制（难以监控和调试）
2. ❌ 缺少状态机（状态转换不清晰）
3. ❌ 缺少错误恢复机制
4. ❌ 缺少审计追踪

---

#### 借鉴建议 1: 引入状态机模式

**设计思路**:
- 定义清晰的状态枚举
- 使用状态机管理状态转换
- 每个状态转换都触发事件

**代码示例**:
```python
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Dict, Any, List, Tuple
import asyncio
from datetime import datetime

class LoopState(Enum):
    """Agent Loop 状态枚举"""
    IDLE = "idle"
    PERCEIVING = "perceiving"
    REASONING = "reasoning"
    VALIDATING = "validating"
    EXECUTING = "executing"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class StateTransition:
    """状态转换"""
    from_state: LoopState
    to_state: LoopState
    event: str
    timestamp: datetime
    metadata: Dict[str, Any] = None

class StateMachine:
    """状态机"""
    
    def __init__(
        self,
        initial_state: LoopState = LoopState.IDLE
    ):
        self._current_state = initial_state
        self._state_transitions: List[StateTransition] = []
        self._transition_handlers: Dict[
            Tuple[LoopState, LoopState],
            Callable
        ] = {}
    
    @property
    def current_state(self) -> LoopState:
        """获取当前状态"""
        return self._current_state
    
    def register_handler(
        self,
        from_state: LoopState,
        to_state: LoopState,
        handler: Callable
    ):
        """注册状态转换处理器"""
        self._transition_handlers[(from_state, to_state)] = handler
    
    async def transition(
        self,
        to_state: LoopState,
        event: str = "",
        metadata: Dict[str, Any] = None
    ):
        """执行状态转换"""
        
        from_state = self._current_state
        
        # 记录转换
        transition = StateTransition(
            from_state=from_state,
            to_state=to_state,
            event=event,
            timestamp=datetime.utcnow(),
            metadata=metadata or {}
        )
        
        self._state_transitions.append(transition)
        
        # 调用处理器
        handler = self._transition_handlers.get(
            (from_state, to_state)
        )
        if handler:
            await handler(transition)
        
        # 更新状态
        self._current_state = to_state
    
    def get_history(self) -> List[StateTransition]:
        """获取状态转换历史"""
        return self._state_transitions

# 使用示例
class EnhancedAgentLoop:
    """增强的 Agent Loop（带状态机）"""
    
    def __init__(self):
        self.state_machine = StateMachine()
        self._setup_state_handlers()
    
    def _setup_state_handlers(self):
        """设置状态转换处理器"""
        
        # IDLE → PERCEIVING
        self.state_machine.register_handler(
            LoopState.IDLE,
            LoopState.PERCEIVING,
            self._on_perceive_start
        )
        
        # PERCEIVING → REASONING
        self.state_machine.register_handler(
            LoopState.PERCEIVING,
            LoopState.REASONING,
            self._on_reason_start
        )
        
        # REASONING → EXECUTING
        self.state_machine.register_handler(
            LoopState.REASONING,
            LoopState.EXECUTING,
            self._on_execute_start
        )
        
        # EXECUTING → COMPLETED
        self.state_machine.register_handler(
            LoopState.EXECUTING,
            LoopState.COMPLETED,
            self._on_complete
        )
        
        # 任意状态 → FAILED
        for state in LoopState:
            if state != LoopState.FAILED:
                self.state_machine.register_handler(
                    state,
                    LoopState.FAILED,
                    self._on_error
                )
    
    async def _on_perceive_start(self, transition: StateTransition):
        """感知开始"""
        logger.info(f"开始感知: {transition.timestamp}")
        # 发送事件
        await event_bus.emit("perceive_start", transition.metadata)
    
    async def _on_reason_start(self, transition: StateTransition):
        """推理开始"""
        logger.info(f"开始推理: {transition.timestamp}")
        await event_bus.emit("reason_start", transition.metadata)
    
    async def _on_execute_start(self, transition: StateTransition):
        """执行开始"""
        logger.info(f"开始执行: {transition.timestamp}")
        await event_bus.emit("execute_start", transition.metadata)
    
    async def _on_complete(self, transition: StateTransition):
        """完成"""
        logger.info(f"执行完成: {transition.timestamp}")
        await event_bus.emit("loop_complete", transition.metadata)
    
    async def _on_error(self, transition: StateTransition):
        """错误"""
        logger.error(f"执行失败: {transition.timestamp}")
        await event_bus.emit("loop_failed", transition.metadata)
    
    async def run(self):
        """运行 Agent Loop"""
        
        # 状态转换: IDLE → PERCEIVING
        await self.state_machine.transition(
            LoopState.PERCEIVING,
            event="loop_start",
            metadata={"turn": 1}
        )
        
        # 感知环境
        perception = await collect_snapshot()
        
        # 状态转换: PERCEIVING → REASONING
        await self.state_machine.transition(
            LoopState.REASONING,
            event="perceive_complete",
            metadata={"perception": perception}
        )
        
        # LLM 推理
        response = await llm.generate(messages + perception)
        
        # 状态转换: REASONING → EXECUTING
        await self.state_machine.transition(
            LoopState.EXECUTING,
            event="reason_complete",
            metadata={"tool_calls": response.tool_calls}
        )
        
        # 执行工具
        for tool_call in response.tool_calls:
            result = await execute_tool(tool_call)
            messages.append(result)
        
        # 状态转换: EXECUTING → COMPLETED
        await self.state_machine.transition(
            LoopState.COMPLETED,
            event="execute_complete",
            metadata={"result": "success"}
        )
```

**借鉴价值**:
- ✅ 清晰的状态管理
- ✅ 状态转换可追溯
- ✅ 易于调试和监控
- ✅ 支持状态转换回调

---

#### 借鉴建议 2: 引入事件驱动架构

**设计思路**:
- 使用 EventBus 解耦模块
- 事件类型明确定义
- 支持事件过滤和路由

**代码示例**:
```python
from typing import Callable, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime
import asyncio

@dataclass
class Event:
    """事件基类"""
    event_type: str
    payload: Dict[str, Any]
    timestamp: datetime
    correlation_id: str = None

class EventBus:
    """事件总线"""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._event_history: List[Event] = []
        self._filters: Dict[str, Callable] = {}
    
    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Event], None]
    ):
        """订阅事件"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    
    def unsubscribe(
        self,
        event_type: str,
        handler: Callable
    ):
        """取消订阅"""
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(handler)
    
    def register_filter(
        self,
        event_type: str,
        filter_func: Callable[[Event], bool]
    ):
        """注册事件过滤器"""
        self._filters[event_type] = filter_func
    
    async def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        correlation_id: str = None
    ):
        """发布事件"""
        
        event = Event(
            event_type=event_type,
            payload=payload,
            timestamp=datetime.utcnow(),
            correlation_id=correlation_id
        )
        
        # 记录事件历史
        self._event_history.append(event)
        
        # 应用过滤器
        filter_func = self._filters.get(event_type)
        if filter_func and not filter_func(event):
            logger.debug(f"事件被过滤: {event_type}")
            return
        
        # 通知订阅者
        if event_type in self._subscribers:
            tasks = []
            for handler in self._subscribers[event_type]:
                tasks.append(asyncio.create_task(handler(event)))
            
            # 并发执行所有处理器
            await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_history(
        self,
        event_type: str = None,
        limit: int = 100
    ) -> List[Event]:
        """获取事件历史"""
        
        events = self._event_history
        
        if event_type:
            events = [
                e for e in events 
                if e.event_type == event_type
            ]
        
        return events[-limit:]

# 全局事件总线
event_bus = EventBus()

# 定义事件类型
class EventTypes:
    # Loop 事件
    LOOP_START = "loop.start"
    LOOP_COMPLETE = "loop.complete"
    LOOP_FAILED = "loop.failed"
    
    # 感知事件
    PERCEIVE_START = "perceive.start"
    PERCEIVE_COMPLETE = "perceive.complete"
    
    # 推理事件
    REASON_START = "reason.start"
    REASON_COMPLETE = "reason.complete"
    
    # 执行事件
    EXECUTE_START = "execute.start"
    EXECUTE_COMPLETE = "execute.complete"
    
    # 工具事件
    TOOL_CALL_START = "tool_call.start"
    TOOL_CALL_COMPLETE = "tool_call.complete"
    TOOL_CALL_FAILED = "tool_call.failed"
    
    # 权限事件
    PERMISSION_CHECK = "permission.check"
    PERMISSION_DENIED = "permission.denied"
    PERMISSION_GRANTED = "permission.granted"
    
    # 安全事件
    INJECTION_DETECTED = "security.injection_detected"
    MALICIOUS_COMMAND = "security.malicious_command"
    
    # 审计事件
    AUDIT_LOG = "audit.log"

# 使用示例
class AuditEventHandler:
    """审计事件处理器"""
    
    async def __call__(self, event: Event):
        """处理审计事件"""
        
        # 写入审计日志
        await db_manager.audit_log.create({
            "event_type": event.event_type,
            "payload": event.payload,
            "timestamp": event.timestamp,
            "correlation_id": event.correlation_id,
        })

# 订阅事件
event_bus.subscribe(
    EventTypes.TOOL_CALL_COMPLETE,
    AuditEventHandler()
)

# 使用过滤器（仅记录高风险操作）
def high_risk_filter(event: Event) -> bool:
    """仅记录高风险操作"""
    risk_level = event.payload.get("risk_level")
    return risk_level == "HIGH"

event_bus.register_filter(
    EventTypes.TOOL_CALL_COMPLETE,
    high_risk_filter
)
```

**借鉴价值**:
- ✅ 模块解耦
- ✅ 易于扩展
- ✅ 支持实时监控
- ✅ 便于调试和追踪

---

### 2.2 ExecutionContext（执行上下文）

#### 设计原理

**职责**:
- 存储执行过程中的所有状态
- 提供状态访问和修改接口
- 支持上下文传递和共享

**设计模式**:
- **Memento Pattern**: 保存和恢复状态
- **Immutable Object**: 部分状态不可变

**关键数据结构**:
```python
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from datetime import datetime

@dataclass
class ExecutionContext:
    """执行上下文"""
    
    # 执行信息
    execution_id: str
    graph_id: str
    graph_version: int
    
    # 用户信息
    user_id: str
    session_id: str
    
    # 工作空间
    workspace_id: str
    workspace_path: str
    
    # 输入数据
    input_data: Dict[str, Any]
    
    # 中间结果
    node_results: Dict[str, Any]
    
    # 状态
    status: str
    current_node_id: str
    
    # 缓存
    cache: Dict[str, Any]
    
    # 时间信息
    started_at: datetime
    completed_at: Optional[datetime]
    
    # 元数据
    metadata: Dict[str, Any]
    
    def get_input(self, key: str, default=None):
        """获取输入数据"""
        return self.input_data.get(key, default)
    
    def set_result(self, node_id: str, result: Any):
        """设置节点结果"""
        self.node_results[node_id] = result
    
    def get_result(self, node_id: str, default=None):
        """获取节点结果"""
        return self.node_results.get(node_id, default)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "execution_id": self.execution_id,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "input_data": self.input_data,
            "node_results": self.node_results,
            "status": self.status,
            "current_node_id": self.current_node_id,
            "cache": self.cache,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }
```

#### OpsAgent 对应实现

**当前实现**:
```python
class LoopState:
    """Loop 状态（简化版）"""
    
    def __init__(self):
        self.messages: list[BaseMessage] = []
        self.turn_count: int = 0
        self.metadata: dict = {}
```

**问题分析**:
1. ❌ 状态字段少，信息不足
2. ❌ 缺少用户和会话信息
3. ❌ 缺少工作空间管理
4. ❌ 缺少时间戳信息
5. ❌ 缺少缓存机制

---

#### 借鉴建议 3: 增强执行上下文设计

**设计思路**:
- 增加更多状态字段
- 支持时间戳追踪
- 支持工作空间管理
- 支持缓存和持久化

**代码示例**:
```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum
import uuid

class LoopStatus(Enum):
    """Loop 状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class ToolExecution:
    """工具执行记录"""
    tool_name: str
    tool_args: Dict[str, Any]
    result: Any
    error: Optional[str] = None
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None
    
    def complete(self, result: Any, error: Optional[str] = None):
        """标记完成"""
        self.end_time = datetime.utcnow()
        self.duration_ms = int(
            (self.end_time - self.start_time).total_seconds() * 1000
        )
        self.result = result
        self.error = error

@dataclass
class LoopContext:
    """增强的 Loop 上下文"""
    
    # 基础信息
    loop_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_id: str = ""
    
    # 状态
    status: LoopStatus = LoopStatus.IDLE
    turn_count: int = 0
    max_turns: int = 40
    
    # 消息
    messages: List[BaseMessage] = field(default_factory=list)
    
    # 工具执行历史
    tool_executions: List[ToolExecution] = field(default_factory=list)
    
    # 快照
    snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # 工作空间
    workspace_path: str = "/tmp/workspace"
    
    # 缓存
    cache: Dict[str, Any] = field(default_factory=dict)
    
    # 权限
    permissions: List[str] = field(default_factory=list)
    
    # 审计
    audit_log: List[Dict[str, Any]] = field(default_factory=list)
    
    # 时间信息
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 性能指标
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    
    def start(self):
        """开始 Loop"""
        self.status = LoopStatus.RUNNING
        self.started_at = datetime.utcnow()
    
    def complete(self):
        """完成 Loop"""
        self.status = LoopStatus.COMPLETED
        self.completed_at = datetime.utcnow()
    
    def fail(self, error: str):
        """失败"""
        self.status = LoopStatus.FAILED
        self.completed_at = datetime.utcnow()
        self.metadata["error"] = error
    
    def add_message(self, message: BaseMessage):
        """添加消息（滑动窗口）"""
        self.messages.append(message)
        
        # 滑动窗口：保留最近 N 条消息
        if len(self.messages) > self.max_turns * 2:
            self.messages = self.messages[-self.max_turns * 2:]
    
    def record_tool_execution(
        self,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> ToolExecution:
        """记录工具执行"""
        execution = ToolExecution(
            tool_name=tool_name,
            tool_args=tool_args,
            result=None,
        )
        self.tool_executions.append(execution)
        self.total_tool_calls += 1
        return execution
    
    def create_snapshot(self, name: str) -> str:
        """创建快照"""
        snapshot_id = f"{name}_{datetime.utcnow().timestamp()}"
        self.snapshots[snapshot_id] = {
            "messages": self.messages.copy(),
            "metadata": self.metadata.copy(),
            "timestamp": datetime.utcnow().isoformat(),
        }
        return snapshot_id
    
    def restore_snapshot(self, snapshot_id: str):
        """恢复快照"""
        if snapshot_id in self.snapshots:
            snapshot = self.snapshots[snapshot_id]
            self.messages = snapshot["messages"].copy()
            self.metadata = snapshot["metadata"].copy()
        else:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
    
    def get_duration_ms(self) -> Optional[int]:
        """获取执行时长"""
        if self.started_at and self.completed_at:
            return int(
                (self.completed_at - self.started_at).total_seconds() * 1000
            )
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "loop_id": self.loop_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "turn_count": self.turn_count,
            "max_turns": self.max_turns,
            "message_count": len(self.messages),
            "tool_execution_count": len(self.tool_executions),
            "snapshot_count": len(self.snapshots),
            "workspace_path": self.workspace_path,
            "permissions": self.permissions,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.get_duration_ms(),
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "total_tokens": self.total_tokens,
            "metadata": self.metadata,
        }
```

**借鉴价值**:
- ✅ 完整的上下文信息
- ✅ 支持快照和恢复
- ✅ 支持性能追踪
- ✅ 易于调试和监控

---

### 2.3 Block（插件化架构）

#### 设计原理

**职责**:
- 定义可复用的执行单元
- 提供统一的接口规范
- 支持可视化组合

**设计模式**:
- **Template Method Pattern**: 定义执行模板
- **Factory Pattern**: 动态创建 Block 实例
- **Strategy Pattern**: 不同 Block 不同策略

**关键数据结构**:
```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from pydantic import BaseModel, Field

class BlockInputSchema(BaseModel):
    """Block 输入 Schema"""
    pass

class BlockOutputSchema(BaseModel):
    """Block 输出 Schema"""
    pass

class BlockSchema(BaseModel):
    """Block Schema（描述 Block 的接口）"""
    
    # 基本信息
    id: str
    name: str
    description: str
    
    # 输入/输出
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    
    # 分类
    category: str
    tags: List[str] = []
    
    # 元数据
    metadata: Dict[str, Any] = {}

class BaseBlock(ABC):
    """Block 基类"""
    
    # Block 唯一标识
    block_id: str = ""
    
    # Block 名称
    name: str = ""
    
    # Block 描述
    description: str = ""
    
    # 输入 Schema
    input_schema: Dict[str, Any] = {}
    
    # 输出 Schema
    output_schema: Dict[str, Any] = {}
    
    # 分类
    category: str = "custom"
    
    # 标签
    tags: List[str] = []
    
    @staticmethod
    def get_schema() -> BlockSchema:
        """获取 Block Schema（静态方法）"""
        raise NotImplementedError()
    
    @abstractmethod
    async def run(
        self,
        input_data: Dict[str, Any],
        execution_context: ExecutionContext
    ) -> Dict[str, Any]:
        """执行 Block（抽象方法）"""
        raise NotImplementedError()
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """验证输入数据"""
        # 使用 Pydantic 验证
        try:
            BlockInputSchema(**input_data)
            return True
        except Exception as e:
            logger.error(f"Input validation failed: {e}")
            return False
    
    def validate_output(self, output_data: Dict[str, Any]) -> bool:
        """验证输出数据"""
        try:
            BlockOutputSchema(**output_data)
            return True
        except Exception as e:
            logger.error(f"Output validation failed: {e}")
            return False
```

#### 具体实现示例

**1. WebSearchBlock（网络搜索）**:
```python
from pydantic import BaseModel, Field

class WebSearchInput(BaseModel):
    """网络搜索输入"""
    query: str = Field(..., description="搜索关键词")
    max_results: int = Field(default=5, description="最大结果数")

class WebSearchOutput(BaseModel):
    """网络搜索输出"""
    results: List[Dict[str, Any]] = Field(..., description="搜索结果")

class WebSearchInputSchema(BaseModel):
    query: str
    max_results: int = 5

class WebSearchOutputSchema(BaseModel):
    results: List[Dict[str, Any]]

class WebSearchBlock(BaseBlock):
    """网络搜索 Block"""
    
    block_id = "web_search"
    name = "Web Search"
    description = "执行网络搜索并返回结果"
    category = "web"
    tags = ["search", "web", "information"]
    
    input_schema = WebSearchInputSchema.model_json_schema()
    output_schema = WebSearchOutputSchema.model_json_schema()
    
    def __init__(self, search_client):
        self._search_client = search_client
    
    @staticmethod
    def get_schema() -> BlockSchema:
        """获取 Schema"""
        return BlockSchema(
            id=WebSearchBlock.block_id,
            name=WebSearchBlock.name,
            description=WebSearchBlock.description,
            input_schema=WebSearchBlock.input_schema,
            output_schema=WebSearchBlock.output_schema,
            category=WebSearchBlock.category,
            tags=WebSearchBlock.tags,
        )
    
    async def run(
        self,
        input_data: Dict[str, Any],
        execution_context: ExecutionContext
    ) -> Dict[str, Any]:
        """执行网络搜索"""
        
        # 验证输入
        if not self.validate_input(input_data):
            raise ValueError("Invalid input data")
        
        # 解析输入
        query = input_data["query"]
        max_results = input_data.get("max_results", 5)
        
        # 执行搜索
        results = await self._search_client.search(
            query=query,
            max_results=max_results
        )
        
        # 返回结果
        output_data = {"results": results}
        
        # 验证输出
        if not self.validate_output(output_data):
            logger.warning("Output validation failed")
        
        return output_data
```

**2. CodeExecutorBlock（代码执行）**:
```python
class CodeExecutorInput(BaseModel):
    """代码执行输入"""
    code: str = Field(..., description="要执行的代码")
    language: str = Field(default="python", description="编程语言")

class CodeExecutorOutput(BaseModel):
    """代码执行输出"""
    output: str = Field(..., description="执行输出")
    error: Optional[str] = Field(default=None, description="错误信息")

class CodeExecutorBlock(BaseBlock):
    """代码执行 Block"""
    
    block_id = "code_executor"
    name = "Code Executor"
    description = "执行代码并返回结果"
    category = "code"
    tags = ["execution", "python", "code"]
    
    input_schema = CodeExecutorInput.model_json_schema()
    output_schema = CodeExecutorOutput.model_json_schema()
    
    def __init__(self, sandbox):
        self._sandbox = sandbox
    
    async def run(
        self,
        input_data: Dict[str, Any],
        execution_context: ExecutionContext
    ) -> Dict[str, Any]:
        """执行代码"""
        
        code = input_data["code"]
        language = input_data.get("language", "python")
        
        # 在沙箱中执行代码
        try:
            output = await self._sandbox.execute(code, language)
            return {"output": output, "error": None}
        except Exception as e:
            return {"output": "", "error": str(e)}
```

#### OpsAgent 对应实现

**当前实现**:
```python
from langchain.tools import tool

@tool
def exec_bash(command: str) -> str:
    """执行 Bash 命令"""
    result = subprocess.run(command, shell=True, capture_output=True)
    return result.stdout.decode()

@tool
def read_file(path: str) -> str:
    """读取文件"""
    with open(path, 'r') as f:
        return f.read()
```

**问题分析**:
1. ❌ 缺少统一的接口规范
2. ❌ 缺少输入/输出验证
3. ❌ 缺少 Schema 定义
4. ❌ 缺少分类和标签
5. ❌ 缺少元数据支持

---

// Part 1/4

#### 借鉴建议 4: 统一工具接口规范

**设计思路**:
- 定义统一的 BaseTool 基类
- 使用 Pydantic 定义输入/输出 Schema
- 支持工具分类和标签
- 支持工具元数据

**代码示例**:
```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum

class ToolRiskLevel(Enum):
    """工具风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ToolCategory(Enum):
    """工具分类"""
    FILE = "file"
    SYSTEM = "system"
    NETWORK = "network"
    CODE = "code"
    WEB = "web"
    DATABASE = "database"

class BaseToolSchema(BaseModel):
    """工具 Schema"""
    
    # 基本信息
    id: str
    name: str
    description: str
    
    # 分类
    category: ToolCategory
    risk_level: ToolRiskLevel
    
    # 标签
    tags: List[str] = []
    
    # 输入/输出
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    
    # 权限
    required_permissions: List[str] = []
    
    # 元数据
    metadata: Dict[str, Any] = {}
    
    model_config = ConfigDict(use_enum_values=True)

class BaseTool(ABC):
    """工具基类（统一接口）"""
    
    # 工具唯一标识
    tool_id: str = ""
    
    # 工具名称
    name: str = ""
    
    # 工具描述
    description: str = ""
    
    # 分类
    category: ToolCategory = ToolCategory.SYSTEM
    
    # 风险等级
    risk_level: ToolRiskLevel = ToolRiskLevel.MEDIUM
    
    # 标签
    tags: List[str] = []
    
    # 所需权限
    required_permissions: List[str] = []
    
    # 输入 Schema
    input_schema: Dict[str, Any] = {}
    
    # 输出 Schema
    output_schema: Dict[str, Any] = {}
    
    # 元数据
    metadata: Dict[str, Any] = {}
    
    @staticmethod
    def get_schema() -> BaseToolSchema:
        """获取工具 Schema"""
        raise NotImplementedError()
    
    @abstractmethod
    async def execute(
        self,
        input_data: Dict[str, Any],
        context: LoopContext
    ) -> Dict[str, Any]:
        """执行工具"""
        raise NotImplementedError()
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """验证输入"""
        try:
            BaseModel(**input_data)
            return True
        except Exception as e:
            logger.error(f"Input validation failed: {e}")
            return False
    
    def validate_output(self, output_data: Dict[str, Any]) -> bool:
        """验证输出"""
        try:
            BaseModel(**output_data)
            return True
        except Exception as e:
            logger.error(f"Output validation failed: {e}")
            return False
    
    def get_risk_level(
        self,
        input_data: Dict[str, Any]
    ) -> ToolRiskLevel:
        """评估风险等级（可重写）"""
        return self.risk_level
```

**具体实现示例**:

**1. ExecBashTool（执行 Bash 命令）**:
```python
from pydantic import BaseModel, Field

class ExecBashInput(BaseModel):
    """执行 Bash 命令输入"""
    command: str = Field(..., description="要执行的命令")
    timeout: int = Field(default=30, description="超时时间（秒）")
    working_dir: Optional[str] = Field(
        default=None,
        description="工作目录"
    )

class ExecBashOutput(BaseModel):
    """执行 Bash 命令输出"""
    stdout: str = Field(..., description="标准输出")
    stderr: str = Field(..., description="标准错误")
    exit_code: int = Field(..., description="退出码")

class ExecBashTool(BaseTool):
    """执行 Bash 命令工具"""
    
    tool_id = "exec_bash"
    name = "Execute Bash Command"
    description = "执行 Bash 命令并返回结果"
    category = ToolCategory.SYSTEM
    risk_level = ToolRiskLevel.HIGH
    tags = ["bash", "command", "system", "shell"]
    required_permissions = ["bash.execute"]
    
    input_schema = ExecBashInput.model_json_schema()
    output_schema = ExecBashOutput.model_json_schema()
    
    metadata = {
        "version": "1.0.0",
        "author": "OpsAgent",
        "documentation": "https://docs.opsagent.com/tools/exec_bash",
    }
    
    def __init__(self):
        self._privilege_broker = PrivilegeBroker()
    
    @staticmethod
    def get_schema() -> BaseToolSchema:
        """获取 Schema"""
        return BaseToolSchema(
            id=ExecBashTool.tool_id,
            name=ExecBashTool.name,
            description=ExecBashTool.description,
            category=ExecBashTool.category,
            risk_level=ExecBashTool.risk_level,
            tags=ExecBashTool.tags,
            required_permissions=ExecBashTool.required_permissions,
            input_schema=ExecBashTool.input_schema,
            output_schema=ExecBashTool.output_schema,
            metadata=ExecBashTool.metadata,
        )
    
    def get_risk_level(
        self,
        input_data: Dict[str, Any]
    ) -> ToolRiskLevel:
        """评估风险等级"""
        command = input_data["command"]
        
        # 检查命令类型
        if any(cmd in command for cmd in ["rm -rf", "dd", "mkfs"]):
            return ToolRiskLevel.CRITICAL
        elif any(cmd in command for cmd in ["rm", "mv", "cp", "chmod"]):
            return ToolRiskLevel.HIGH
        elif any(cmd in command for cmd in ["cat", "grep", "ls", "pwd"]):
            return ToolRiskLevel.LOW
        else:
            return ToolRiskLevel.MEDIUM
    
    async def execute(
        self,
        input_data: Dict[str, Any],
        context: LoopContext
    ) -> Dict[str, Any]:
        """执行 Bash 命令"""
        
        # 验证输入
        if not self.validate_input(input_data):
            raise ValueError("Invalid input data")
        
        # 解析输入
        command = input_data["command"]
        timeout = input_data.get("timeout", 30)
        working_dir = input_data.get("working_dir")
        
        # 评估风险
        risk_level = self.get_risk_level(input_data)
        
        # 最小权限执行
        result = await self._privilege_broker.execute_command(
            command=command,
            risk_level=risk_level.value,
            timeout=timeout,
            working_dir=working_dir
        )
        
        # 返回结果
        output_data = {
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "exit_code": result.returncode,
        }
        
        # 验证输出
        if not self.validate_output(output_data):
            logger.warning("Output validation failed")
        
        return output_data
```

**2. FileReadTool（读取文件）**:
```python
class FileReadInput(BaseModel):
    """读取文件输入"""
    path: str = Field(..., description="文件路径")
    offset: Optional[int] = Field(default=0, description="读取起始位置")
    limit: Optional[int] = Field(default=None, description="读取最大行数")

class FileReadOutput(BaseModel):
    """读取文件输出"""
    content: str = Field(..., description="文件内容")
    line_count: int = Field(..., description="读取的行数")
    total_lines: int = Field(..., description="文件总行数")

class FileReadTool(BaseTool):
    """读取文件工具"""
    
    tool_id = "file_read"
    name = "Read File"
    description = "读取文件内容"
    category = ToolCategory.FILE
    risk_level = ToolRiskLevel.LOW
    tags = ["file", "read", "io"]
    required_permissions = ["file.read"]
    
    input_schema = FileReadInput.model_json_schema()
    output_schema = FileReadOutput.model_json_schema()
    
    async def execute(
        self,
        input_data: Dict[str, Any],
        context: LoopContext
    ) -> Dict[str, Any]:
        """读取文件"""
        
        path = input_data["path"]
        offset = input_data.get("offset", 0)
        limit = input_data.get("limit")
        
        # 读取文件
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        
        # 切片
        if limit:
            content_lines = lines[offset:offset+limit]
        else:
            content_lines = lines[offset:]
        
        content = "".join(content_lines)
        
        return {
            "content": content,
            "line_count": len(content_lines),
            "total_lines": total_lines,
        }
```

**3. 工具注册表**:
```python
class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool):
        """注册工具"""
        self._tools[tool.tool_id] = tool
        logger.info(f"Tool registered: {tool.tool_id}")
    
    def unregister(self, tool_id: str):
        """注销工具"""
        if tool_id in self._tools:
            del self._tools[tool_id]
            logger.info(f"Tool unregistered: {tool_id}")
    
    def get(self, tool_id: str) -> Optional[BaseTool]:
        """获取工具"""
        return self._tools.get(tool_id)
    
    def list_all(self) -> List[BaseToolSchema]:
        """列出所有工具"""
        return [
            tool.get_schema()
            for tool in self._tools.values()
        ]
    
    def filter_by_category(
        self,
        category: ToolCategory
    ) -> List[BaseToolSchema]:
        """按分类过滤"""
        return [
            tool.get_schema()
            for tool in self._tools.values()
            if tool.category == category
        ]
    
    def filter_by_risk_level(
        self,
        risk_level: ToolRiskLevel
    ) -> List[BaseToolSchema]:
        """按风险等级过滤"""
        return [
            tool.get_schema()
            for tool in self._tools.values()
            if tool.risk_level == risk_level
        ]

# 全局工具注册表
tool_registry = ToolRegistry()

# 注册工具
tool_registry.register(ExecBashTool())
tool_registry.register(FileReadTool())
```

**借鉴价值**:
- ✅ 统一接口规范
- ✅ 输入/输出验证
- ✅ 风险评估
- ✅ 易于扩展和管理

---

### 2.4 MemorySystem（记忆系统）

#### 设计原理

**职责**:
- 存储长期记忆（知识图谱）
- 支持向量搜索
- 管理短期记忆（会话）

**设计模式**:
- **Repository Pattern**: 抽象数据访问
- **Strategy Pattern**: 不同记忆类型不同策略

**关键数据结构**:
```python
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

class MemoryType(Enum):
    """记忆类型"""
    FACT = "fact"           # 事实
    PREFERENCE = "preference"  # 偏好
    EXPERIENCE = "experience"  # 经验
    KNOWLEDGE = "knowledge"    # 知识
    CONTEXT = "context"        # 上下文

@dataclass
class MemoryEpisode:
    """记忆片段"""
    
    # 基本信息
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    
    # 分类
    memory_type: MemoryType = MemoryType.FACT
    
    # 嵌入向量
    embedding: Optional[List[float]] = None
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 时间信息
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    
    # 重要性分数
    importance_score: float = 0.5
    
    # 访问次数
    access_count: int = 0
    
    # 关联的记忆
    related_episode_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "episode_id": self.episode_id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "importance_score": self.importance_score,
            "access_count": self.access_count,
            "related_episode_ids": self.related_episode_ids,
        }

class MemorySystem:
    """记忆系统"""
    
    def __init__(
        self,
        embedding_client,
        vector_db_client,
    ):
        self._embedding_client = embedding_client
        self._vector_db_client = vector_db_client
    
    async def add_episode(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        metadata: Dict[str, Any] = None
    ) -> str:
        """添加记忆片段"""
        
        # 生成嵌入向量
        embedding = await self._embedding_client.embed(content)
        
        # 创建记忆片段
        episode = MemoryEpisode(
            content=content,
            memory_type=memory_type,
            embedding=embedding,
            metadata=metadata or {},
        )
        
        # 存储到向量数据库
        await self._vector_db_client.insert({
            "episode_id": episode.episode_id,
            "content": episode.content,
            "memory_type": episode.memory_type.value,
            "embedding": episode.embedding,
            "metadata": episode.metadata,
            "created_at": episode.created_at,
            "importance_score": episode.importance_score,
            "access_count": episode.access_count,
        })
        
        return episode.episode_id
    
    async def search(
        self,
        query: str,
        top_k: int = 5,
        memory_type: Optional[MemoryType] = None,
        min_score: float = 0.7
    ) -> List[MemoryEpisode]:
        """搜索记忆"""
        
        # 生成查询嵌入
        query_embedding = await self._embedding_client.embed(query)
        
        # 构建过滤条件
        filters = {}
        if memory_type:
            filters["memory_type"] = memory_type.value
        
        # 向量搜索
        results = await self._vector_db_client.search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
            min_score=min_score
        )
        
        # 转换为 MemoryEpisode
        episodes = []
        for result in results:
            episode = MemoryEpisode(
                episode_id=result["episode_id"],
                content=result["content"],
                memory_type=MemoryType(result["memory_type"]),
                embedding=result["embedding"],
                metadata=result["metadata"],
                created_at=result["created_at"],
                importance_score=result.get("importance_score", 0.5),
                access_count=result.get("access_count", 0),
            )
            episodes.append(episode)
        
        return episodes
    
    async def get_episode(
        self,
        episode_id: str
    ) -> Optional[MemoryEpisode]:
        """获取记忆片段"""
        data = await self._vector_db_client.get(episode_id)
        if not data:
            return None
        
        return MemoryEpisode(
            episode_id=data["episode_id"],
            content=data["content"],
            memory_type=MemoryType(data["memory_type"]),
            embedding=data["embedding"],
            metadata=data["metadata"],
            created_at=data["created_at"],
            importance_score=data.get("importance_score", 0.5),
            access_count=data.get("access_count", 0),
        )
    
    async def update_episode(
        self,
        episode_id: str,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """更新记忆片段"""
        episode = await self.get_episode(episode_id)
        if not episode:
            raise ValueError(f"Episode not found: {episode_id}")
        
        # 更新内容
        if content:
            episode.content = content
            episode.embedding = await self._embedding_client.embed(content)
        
        # 更新元数据
        if metadata:
            episode.metadata.update(metadata)
        
        episode.updated_at = datetime.utcnow()
        
        # 保存到向量数据库
        await self._vector_db_client.update(
            episode_id,
            {
                "content": episode.content,
                "embedding": episode.embedding,
                "metadata": episode.metadata,
                "updated_at": episode.updated_at,
            }
        )
    
    async def delete_episode(self, episode_id: str):
        """删除记忆片段"""
        await self._vector_db_client.delete(episode_id)
```

#### OpsAgent 对应实现

**当前实现**:
```python
class MemoryManager:
    """简单的内存管理器"""
    
    MAX_MESSAGES = 40
    
    def __init__(self):
        self.messages: list = []
    
    def add_message(self, message):
        self.messages.append(message)
        if len(self.messages) > self.MAX_MESSAGES:
            self.messages.pop(0)
```

**问题分析**:
1. ❌ 仅支持短期记忆
2. ❌ 无长期记忆存储
3. ❌ 无向量搜索能力
4. ❌ 无记忆分类和元数据

---

#### 借鉴建议 5: 实现双层记忆系统

**设计思路**:
- 短期记忆：LoopContext（滑动窗口）
- 长期记忆：MemorySystem（向量数据库）
- 支持记忆分类和搜索

**代码示例**:
```python
class DualMemorySystem:
    """双层记忆系统"""
    
    def __init__(
        self,
        embedding_client,
        vector_db_client,
    ):
        # 短期记忆（内存）
        self._short_term = LoopContext()
        
        # 长期记忆（向量数据库）
        self._long_term = MemorySystem(
            embedding_client=embedding_client,
            vector_db_client=vector_db_client,
        )
    
    async def add_short_term(self, message: BaseMessage):
        """添加短期记忆"""
        self._short_term.add_message(message)
    
    async def add_long_term(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        metadata: Dict[str, Any] = None
    ) -> str:
        """添加长期记忆"""
        return await self._long_term.add_episode(
            content=content,
            memory_type=memory_type,
            metadata=metadata
        )
    
    async def search_long_term(
        self,
        query: str,
        top_k: int = 5,
        memory_type: Optional[MemoryType] = None
    ) -> List[MemoryEpisode]:
        """搜索长期记忆"""
        return await self._long_term.search(
            query=query,
            top_k=top_k,
            memory_type=memory_type
        )
    
    async def get_context_with_memory(
        self,
        query: str = "",
        include_long_term: bool = True
    ) -> List[BaseMessage]:
        """获取包含记忆的上下文"""
        
        context = self._short_term.messages.copy()
        
        # 如果启用了长期记忆，搜索相关记忆
        if include_long_term and query:
            episodes = await self.search_long_term(query, top_k=3)
            
            for episode in episodes:
                # 将记忆转换为系统消息
                memory_msg = SystemMessage(
                    content=f"[记忆] {episode.content} (类型: {episode.memory_type.value})"
                )
                context.append(memory_msg)
        
        return context
    
    async def consolidate_to_long_term(
        self,
        min_importance: float = 0.7
    ):
        """将重要信息从短期记忆转移到长期记忆"""
        
        for message in self._short_term.messages:
            if isinstance(message, HumanMessage):
                # 提取关键信息
                await self._long_term.add_episode(
                    content=message.content,
                    memory_type=MemoryType.EXPERIENCE,
                    metadata={"source": "short_term"}
                )
```

**借鉴价值**:
- ✅ 短期 + 长期双层记忆
- ✅ 向量搜索能力
- ✅ 记忆分类管理
- ✅ 智能记忆整合

---

### 2.5 BillingTracker（成本追踪）

#### 设计原理

**职责**:
- 追踪 LLM 调用成本
- 按模型和会话统计
- 生成成本报告

**关键数据结构**:
```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

# 模型定价（示例）
MODEL_PRICING = {
    "gpt-4": {
        "input_cost": 0.03,      # $/1K tokens
        "output_cost": 0.06,     # $/1K tokens
    },
    "gpt-3.5-turbo": {
        "input_cost": 0.0005,
        "output_cost": 0.0015,
    },
    "claude-3-opus": {
        "input_cost": 0.015,
        "output_cost": 0.075,
    },
    "deepseek-chat": {
        "input_cost": 0.00014,
        "output_cost": 0.00028,
    },
}

@dataclass
class CostRecord:
    """成本记录"""
    
    # 基本信息
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    execution_id: str = ""
    
    # 模型信息
    model: str = ""
    
    # Token 统计
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # 成本
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    
    # 时间信息
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def create(
        cls,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: str = "",
        execution_id: str = "",
        metadata: Dict[str, Any] = None
    ) -> "CostRecord":
        """创建成本记录"""
        
        # 获取定价
        pricing = MODEL_PRICING.get(model, {})
        input_cost_per_1k = pricing.get("input_cost", 0)
        output_cost_per_1k = pricing.get("output_cost", 0)
        
        # 计算成本
        input_cost = input_tokens * input_cost_per_1k / 1000
        output_cost = output_tokens * output_cost_per_1k / 1000
        total_cost = input_cost + output_cost
        
        return cls(
            session_id=session_id,
            execution_id=execution_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            metadata=metadata or {},
        )

class BillingTracker:
    """成本追踪器"""
    
    def __init__(self):
        self._cost_records: List[CostRecord] = []
    
    async def record_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: str = "",
        execution_id: str = "",
        metadata: Dict[str, Any] = None
    ):
        """记录成本"""
        record = CostRecord.create(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=session_id,
            execution_id=execution_id,
            metadata=metadata
        )
        self._cost_records.append(record)
        logger.info(
            f"Cost recorded: {model} - "
            f"{record.total_tokens} tokens - ¥{record.total_cost:.4f}"
        )
    
    def get_total_cost(
        self,
        session_id: str = ""
    ) -> float:
        """获取总成本"""
        if not session_id:
            return sum(r.total_cost for r in self._cost_records)
        else:
            return sum(
                r.total_cost
                for r in self._cost_records
                if r.session_id == session_id
            )
    
    def get_cost_by_model(self) -> Dict[str, Dict[str, Any]]:
        """按模型统计成本"""
        stats: Dict[str, Dict[str, Any]] = {}
        
        for record in self._cost_records:
            if record.model not in stats:
                stats[record.model] = {
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "call_count": 0,
                }
            
            stats[record.model]["total_cost"] += record.total_cost
            stats[record.model]["total_tokens"] += record.total_tokens
            stats[record.model]["call_count"] += 1
        
        return stats
    
    def get_cost_by_session(self) -> Dict[str, Dict[str, Any]]:
        """按会话统计成本"""
        stats: Dict[str, Dict[str, Any]] = {}
        
        for record in self._cost_records:
            session_id = record.session_id or "default"
            
            if session_id not in stats:
                stats[session_id] = {
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "call_count": 0,
                }
            
            stats[session_id]["total_cost"] += record.total_cost
            stats[session_id]["total_tokens"] += record.total_tokens
            stats[session_id]["call_count"] += 1
        
        return stats
    
    def generate_report(
        self,
        session_id: str = ""
    ) -> str:
        """生成成本报告"""
        
        lines = ["=" * 60]
        lines.append("成本追踪报告")
        lines.append("=" * 60)
        lines.append("")
        
        # 总成本
        total_cost = self.get_total_cost(session_id)
        lines.append(f"总成本: ¥{total_cost:.4f}")
        lines.append("")
        
        # 按模型统计
        lines.append("-" * 60)
        lines.append("按模型统计:")
        lines.append("-" * 60)
        
        model_stats = self.get_cost_by_model()
        for model, stats in model_stats.items():
            lines.append(f"  {model}:")
            lines.append(f"    成本: ¥{stats['total_cost']:.4f}")
            lines.append(f"    Tokens: {stats['total_tokens']:,}")
            lines.append(f"    调用次数: {stats['call_count']}")
        
        lines.append("")
        
        # 按会话统计
        lines.append("-" * 60)
        lines.append("按会话统计:")
        lines.append("-" * 60)
        
        session_stats = self.get_cost_by_session()
        for sid, stats in session_stats.items():
            lines.append(f"  {sid}:")
            lines.append(f"    成本: ¥{stats['total_cost']:.4f}")
            lines.append(f"    Tokens: {stats['total_tokens']:,}")
            lines.append(f"    调用次数: {stats['call_count']}")
        
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    async def export_to_csv(self, filepath: str):
        """导出到 CSV"""
        import csv
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "record_id", "session_id", "execution_id",
                "model", "input_tokens", "output_tokens",
                "total_tokens", "input_cost", "output_cost",
                "total_cost", "timestamp"
            ])
            
            for record in self._cost_records:
                writer.writerow([
                    record.record_id,
                    record.session_id,
                    record.execution_id,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.total_tokens,
                    record.input_cost,
                    record.output_cost,
                    record.total_cost,
                    record.timestamp.isoformat(),
                ])
```

#### OpsAgent 对应实现

**当前实现**:
```python
# 无成本追踪
```

**问题分析**:
1. ❌ 无成本追踪
2. ❌ 无法评估预算
3. ❌ 无成本优化建议

---

#### 借鉴建议 6: 实现成本追踪系统

**代码示例**:
```python
# 为 OpsAgent 添加成本追踪
class OpsAgentBillingTracker:
    """OpsAgent 成本追踪器"""
    
    def __init__(self):
        self._tracker = BillingTracker()
    
    async def record_llm_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        session_id: str = ""
    ):
        """记录 LLM 调用"""
        await self._tracker.record_cost(
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            session_id=session_id,
            metadata={"type": "llm_call"}
        )
    
    def get_cost_report(self, session_id: str = "") -> str:
        """获取成本报告"""
        return self._tracker.generate_report(session_id)
    
    async def export_billing_data(self, filepath: str):
        """导出计费数据"""
        await self._tracker.export_to_csv(filepath)

# 在 Agent Loop 中使用
billing_tracker = OpsAgentBillingTracker()

async def generate_with_tracking(
    llm,
    messages: List[BaseMessage],
    session_id: str
) -> BaseMessage:
    """带成本追踪的 LLM 生成"""
    
    # 记录 Token 统计（假设 LLM 返回）
    response = await llm.ainvoke(messages)
    
    # 记录成本
    await billing_tracker.record_llm_call(
        model=llm.model_name,
        prompt_tokens=response.usage_metadata["input_tokens"],
        completion_tokens=response.usage_metadata["output_tokens"],
        session_id=session_id
    )
    
    return response
```

**借鉴价值**:
- ✅ 成本透明化
- ✅ 预算控制
- ✅ 成本优化建议

---

// Part 2/4

## 三、关键数据结构深度剖析

### 3.1 Graph（工作流定义）

#### 设计原理

**职责**:
- 定义 Agent 的工作流结构
- 描述节点和边的关系
- 支持可视化展示

**关键数据结构**:
```python
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class GraphNode(BaseModel):
    """图节点"""
    
    node_id: str = Field(..., description="节点 ID")
    block_id: str = Field(..., description="Block ID")
    position: Dict[str, float] = Field(
        default_factory=lambda: {"x": 0, "y": 0},
        description="节点位置"
    )
    inputs: Dict[str, Any] = Field(default_factory=dict)
    is_static: bool = Field(default=False, description="是否静态节点")
    constants: List[Dict[str, Any]] = Field(default_factory=list)

class GraphEdge(BaseModel):
    """图边"""
    
    source_id: str = Field(..., description="源节点 ID")
    target_id: str = Field(..., description="目标节点 ID")
    source_handle: str = Field(default="output", description="源端点")
    target_handle: str = Field(default="input", description="目标端点")

class Graph(BaseModel):
    """工作流图"""
    
    graph_id: str = Field(..., description="图 ID")
    name: str = Field(..., description="图名称")
    description: str = Field(default="", description="图描述")
    version: int = Field(default=1, description="版本号")
    
    # 结构
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    
    # 输入/输出
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    
    # 执行配置
    execution_config: Dict[str, Any] = Field(default_factory=dict)
    
    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def validate_graph(self) -> bool:
        """验证图的合法性"""
        
        # 检查节点 ID 唯一性
        node_ids = [n.node_id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("节点 ID 重复")
        
        # 检查边的连接有效性
        node_ids_set = set(node_ids)
        for edge in self.edges:
            if edge.source_id not in node_ids_set:
                raise ValueError(f"边源节点不存在: {edge.source_id}")
            if edge.target_id not in node_ids_set:
                raise ValueError(f"边目标节点不存在: {edge.target_id}")
        
        return True
    
    def get_execution_order(self) -> List[str]:
        """获取执行顺序（拓扑排序）"""
        
        # 构建邻接表
        adjacency: Dict[str, List[str]] = {}
        in_degree: Dict[str, int] = {}
        
        for node in self.nodes:
            adjacency[node.node_id] = []
            in_degree[node.node_id] = 0
        
        for edge in self.edges:
            adjacency[edge.source_id].append(edge.target_id)
            in_degree[edge.target_id] += 1
        
        # 拓扑排序
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order = []
        
        while queue:
            node_id = queue.pop(0)
            order.append(node_id)
            
            for neighbor in adjacency[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order
```

#### OpsAgent 对应实现

**当前实现**:
```python
# OpsAgent 无图结构，使用 Prompt 定义
```

---

#### 借鉴建议 7: 引入工作流定义机制

**设计思路**:
- 支持 Prompt 定义（简单模式）
- 支持 Graph 定义（高级模式）
- 两者可以转换

**代码示例**:
```python
class OpsAgentGraph:
    """OpsAgent 工作流图（简化版）"""
    
    def __init__(self):
        self.tasks: List[Dict[str, Any]] = []
        self.dependencies: Dict[str, List[str]] = {}
    
    def add_task(
        self,
        task_id: str,
        description: str,
        tools: List[str] = None
    ):
        """添加任务"""
        self.tasks.append({
            "task_id": task_id,
            "description": description,
            "tools": tools or [],
        })
        self.dependencies[task_id] = []
    
    def add_dependency(
        self,
        task_id: str,
        depends_on: str
    ):
        """添加依赖"""
        self.dependencies[task_id].append(depends_on)
    
    def to_prompt(self) -> str:
        """转换为 Prompt"""
        prompt_lines = ["# 工作流定义\n"]
        
        for i, task in enumerate(self.tasks, 1):
            prompt_lines.append(f"## 任务 {i}: {task['task_id']}")
            prompt_lines.append(f"描述: {task['description']}")
            
            if task['tools']:
                prompt_lines.append(f"可用工具: {', '.join(task['tools'])}")
            
            deps = self.dependencies.get(task['task_id'], [])
            if deps:
                prompt_lines.append(f"依赖: {', '.join(deps)}")
            
            prompt_lines.append("")
        
        return "\n".join(prompt_lines)
    
    def from_prompt(self, prompt: str) -> "OpsAgentGraph":
        """从 Prompt 解析"""
        # 简化实现：解析 Prompt 提取任务
        lines = prompt.split('\n')
        
        current_task = None
        for line in lines:
            if line.startswith("## 任务"):
                if current_task:
                    self.add_task(
                        task_id=current_task["id"],
                        description=current_task["desc"],
                        tools=current_task["tools"]
                    )
                # 解析任务 ID
                parts = line.split(":")
                current_task = {"id": parts[1].strip(), "desc": "", "tools": []}
            elif "描述:" in line and current_task:
                current_task["desc"] = line.split("描述:")[1].strip()
            elif "可用工具:" in line and current_task:
                tools_str = line.split("可用工具:")[1].strip()
                current_task["tools"] = [t.strip() for t in tools_str.split(",")]
        
        return self
```

**借鉴价值**:
- ✅ 结构化任务定义
- ✅ 可视化展示
- ✅ 可追踪执行顺序

---

### 3.2 ExecutionContext（执行上下文）- 深度剖析

#### 详细设计

```python
@dataclass
class ExecutionContext:
    """执行上下文（详细版）"""
    
    # ========== 基础信息 ==========
    execution_id: str
    graph_id: str
    graph_version: int
    
    # ========== 用户信息 ==========
    user_id: str
    user_email: str
    session_id: str
    tenant_id: str  # 多租户
    
    # ========== 工作空间 ==========
    workspace_id: str
    workspace_name: str
    workspace_path: str
    
    # ========== 输入数据 ==========
    input_data: Dict[str, Any]
    
    # ========== 中间结果 ==========
    node_results: Dict[str, Any]
    
    # ========== 状态 ==========
    status: str
    current_node_id: str
    current_node_index: int
    
    # ========== 缓存 ==========
    cache: Dict[str, Any]
    cache_ttl: Dict[str, datetime]  # 缓存过期时间
    
    # ========== 工具调用记录 ==========
    tool_calls: List[Dict[str, Any]]
    
    # ========== 错误信息 ==========
    errors: List[Dict[str, Any]]
    
    # ========== 时间信息 ==========
    started_at: datetime
    completed_at: Optional[datetime]
    
    # ========== 性能指标 ==========
    node_timings: Dict[str, float]  # 节点执行时间（毫秒）
    total_duration_ms: Optional[float]
    
    # ========== 资源使用 ==========
    memory_usage_mb: Optional[float]
    cpu_usage_percent: Optional[float]
    
    # ========== 元数据 ==========
    metadata: Dict[str, Any]
    
    def get_input(self, key: str, default=None):
        """获取输入数据"""
        return self.input_data.get(key, default)
    
    def set_result(self, node_id: str, result: Any):
        """设置节点结果"""
        self.node_results[node_id] = result
    
    def get_result(self, node_id: str, default=None):
        """获取节点结果"""
        return self.node_results.get(node_id, default)
    
    def set_cache(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 3600
    ):
        """设置缓存"""
        self.cache[key] = value
        self.cache_ttl[key] = datetime.utcnow() + timedelta(
            seconds=ttl_seconds
        )
    
    def get_cache(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if key not in self.cache:
            return None
        
        # 检查是否过期
        expiry = self.cache_ttl.get(key)
        if expiry and datetime.utcnow() > expiry:
            del self.cache[key]
            del self.cache_ttl[key]
            return None
        
        return self.cache[key]
    
    def record_error(
        self,
        node_id: str,
        error: Exception
    ):
        """记录错误"""
        self.errors.append({
            "node_id": node_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def record_timing(
        self,
        node_id: str,
        duration_ms: float
    ):
        """记录节点执行时间"""
        self.node_timings[node_id] = duration_ms
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "execution_id": self.execution_id,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "workspace_path": self.workspace_path,
            "input_data": self.input_data,
            "node_results": self.node_results,
            "status": self.status,
            "current_node_id": self.current_node_id,
            "current_node_index": self.current_node_index,
            "cache_keys": list(self.cache.keys()),
            "tool_call_count": len(self.tool_calls),
            "error_count": len(self.errors),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "node_timings": self.node_timings,
            "total_duration_ms": self.total_duration_ms,
            "memory_usage_mb": self.memory_usage_mb,
            "cpu_usage_percent": self.cpu_usage_percent,
            "metadata": self.metadata,
        }
```

---

### 3.3 其他关键数据结构

#### CompactionTracker（上下文压缩追踪器）

```python
@dataclass
class CompactStep:
    """压缩步骤"""
    step_number: int
    original_message_count: int
    compacted_message_count: int
    strategy_used: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

class CompactionTracker:
    """上下文压缩追踪器"""
    
    def __init__(self, max_compact_retries: int = 2):
        self._max_compact_retries = max_compact_retries
        self._compact_steps: List[CompactStep] = []
    
    async def compact_messages(
        self,
        messages: List[BaseMessage]
    ) -> List[BaseMessage]:
        """压缩消息（多级压缩）"""
        
        original_count = len(messages)
        
        # 尝试压缩
        for attempt in range(self._max_compact_retries):
            try:
                # 等级 1: 提取关键信息 + LLM 摘要
                if attempt == 0:
                    key_points = self._extract_key_points(messages)
                    summary = await llm.generate(
                        f"摘要以下内容: {key_points}"
                    )
                    compacted = [summary] + messages[-5:]
                
                # 等级 2: 仅保留最近 N 条
                elif attempt == 1:
                    compacted = messages[-10:]
                
                # 记录压缩步骤
                self._compact_steps.append(
                    CompactStep(
                        step_number=attempt + 1,
                        original_message_count=original_count,
                        compacted_message_count=len(compacted),
                        strategy_used=f"strategy_{attempt}",
                    )
                )
                
                return compacted
            
            except Exception as e:
                logger.error(f"压缩失败 (attempt {attempt + 1}): {e}")
        
        # 返回原始消息
        return messages
    
    def _extract_key_points(self, messages: List[BaseMessage]) -> str:
        """提取关键点"""
        # 简化实现：提取工具调用结果
        key_points = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                key_points.append(f"Tool: {msg.name}, Result: {msg.content}")
        return "\n".join(key_points)
    
    def get_compact_history(self) -> List[CompactStep]:
        """获取压缩历史"""
        return self._compact_steps
```

---

## 四、OpsAgent 可借鉴的设计模式

### 4.1 Repository Pattern（仓储模式）

**应用场景**: 数据访问层抽象

**AutoGPT 实现**:
```python
class GraphRepository:
    """图仓储"""
    
    def __init__(self, db_client):
        self._db = db_client
    
    async def get_by_id(
        self,
        graph_id: str
    ) -> Optional[Graph]:
        """根据 ID 获取图"""
        data = await self._db.graph.find_unique(
            where={"id": graph_id}
        )
        if not data:
            return None
        return Graph(**data)
    
    async def save(self, graph: Graph) -> Graph:
        """保存图"""
        return await self._db.graph.upsert(
            data=graph.model_dump()
        )
    
    async def list_by_user(
        self,
        user_id: str
    ) -> List[Graph]:
        """根据用户 ID 列出图"""
        data = await self._db.graph.find_many(
            where={"user_id": user_id}
        )
        return [Graph(**d) for d in data]
```

**OpsAgent 借鉴**:
```python
class ToolExecutionRepository:
    """工具执行仓储"""
    
    def __init__(self, db_client):
        self._db = db_client
    
    async def save_execution(
        self,
        execution: ToolExecution
    ) -> ToolExecution:
        """保存执行记录"""
        return await self._db.tool_execution.create(
            data=execution.to_dict()
        )
    
    async def get_executions_by_session(
        self,
        session_id: str
    ) -> List[ToolExecution]:
        """获取会话的所有执行"""
        data = await self._db.tool_execution.find_many(
            where={"session_id": session_id}
        )
        return [ToolExecution(**d) for d in data]
```

---

### 4.2 Strategy Pattern（策略模式）

**应用场景**: 不同的执行策略

**AutoGPT 实现**:
```python
class ExecutionStrategy(ABC):
    """执行策略（抽象）"""
    
    @abstractmethod
    async def execute(
        self,
        graph: Graph,
        context: ExecutionContext
    ):
        pass

class SequentialStrategy(ExecutionStrategy):
    """顺序执行策略"""
    
    async def execute(
        self,
        graph: Graph,
        context: ExecutionContext
    ):
        order = graph.get_execution_order()
        for node_id in order:
            node = next(n for n in graph.nodes if n.node_id == node_id)
            result = await execute_node(node, context)
            context.set_result(node_id, result)

class ParallelStrategy(ExecutionStrategy):
    """并行执行策略"""
    
    async def execute(
        self,
        graph: Graph,
        context: ExecutionContext
    ):
        # 并行执行无依赖的节点
        tasks = []
        for node in graph.nodes:
            task = asyncio.create_task(
                execute_node(node, context)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks)
```

**OpsAgent 借鉴**:
```python
class LoopStrategy(ABC):
    """Loop 执行策略"""
    
    @abstractmethod
    async def run(self, loop: EnhancedAgentLoop):
        pass

class StandardLoopStrategy(LoopStrategy):
    """标准 Loop 策略"""
    
    async def run(self, loop: EnhancedAgentLoop):
        while loop.turn_count < loop.max_turns:
            # 感知
            await loop.state_machine.transition(
                LoopState.PERCEIVING
            )
            perception = await collect_snapshot()
            
            # 推理
            await loop.state_machine.transition(
                LoopState.REASONING
            )
            response = await llm.generate(
                loop.context.messages + perception
            )
            
            # 执行
            await loop.state_machine.transition(
                LoopState.EXECUTING
            )
            for tool_call in response.tool_calls:
                result = await execute_tool(tool_call)
                loop.context.add_message(result)
            
            loop.turn_count += 1
```

---

### 4.3 Observer Pattern（观察者模式）

**应用场景**: 事件驱动架构

**AutoGPT 实现**:
```python
class EventObserver(ABC):
    """事件观察者（抽象）"""
    
    @abstractmethod
    async def on_event(self, event: Event):
        pass

class LoggingObserver(EventObserver):
    """日志观察者"""
    
    async def on_event(self, event: Event):
        logger.info(f"Event: {event.event_type} - {event.payload}")

class MetricsObserver(EventObserver):
    """指标观察者"""
    
    async def on_event(self, event: Event):
        # 记录指标
        metrics.increment(f"event.{event.event_type}")
```

**OpsAgent 借鉴**:
```python
class LoopObserver(ABC):
    """Loop 观察者"""
    
    @abstractmethod
    async def on_state_change(
        self,
        from_state: LoopState,
        to_state: LoopState
    ):
        pass
    
    @abstractmethod
    async def on_tool_call(self, tool_name: str, args: dict):
        pass

class AuditObserver(LoopObserver):
    """审计观察者"""
    
    async def on_state_change(
        self,
        from_state: LoopState,
        to_state: LoopState
    ):
        await db_manager.audit_log.create({
            "event": "state_change",
            "from": from_state.value,
            "to": to_state.value,
            "timestamp": datetime.utcnow(),
        })
    
    async def on_tool_call(self, tool_name: str, args: dict):
        await db_manager.audit_log.create({
            "event": "tool_call",
            "tool": tool_name,
            "args": args,
            "timestamp": datetime.utcnow(),
        })
```

---

// Part 3/4

## 五、具体实现建议

### 5.1 立即可实施的改进（高优先级）

#### 改进 1: 增强 LoopContext
```python
# 立即实施：添加更多字段到 LoopContext
@dataclass
class EnhancedLoopContext:
    loop_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_id: str = ""
    status: LoopStatus = LoopStatus.IDLE
    turn_count: int = 0
    max_turns: int = 40
    messages: List[BaseMessage] = field(default_factory=list)
    tool_executions: List[ToolExecution] = field(default_factory=list)
    snapshots: Dict[str, Dict] = field(default_factory=dict)
    workspace_path: str = "/tmp/workspace"
    cache: Dict[str, Any] = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    audit_log: List[Dict] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
```

#### 改进 2: 添加 EventBus
```python
# 立即实施：添加事件总线
event_bus = EventBus()

class EventTypes:
    LOOP_START = "loop.start"
    LOOP_COMPLETE = "loop.complete"
    TOOL_CALL_START = "tool_call.start"
    TOOL_CALL_COMPLETE = "tool_call.complete"

# 订阅审计事件
event_bus.subscribe(
    EventTypes.TOOL_CALL_COMPLETE,
    AuditEventHandler()
)
```

#### 改进 3: 统一工具接口
```python
# 立即实施：重构工具为统一接口
class BaseTool(ABC):
    tool_id: str
    name: str
    description: str
    category: ToolCategory
    risk_level: ToolRiskLevel
    input_schema: Dict
    output_schema: Dict
    
    @abstractmethod
    async def execute(self, input_data, context):
        pass
```

### 5.2 中期改进（中优先级）

#### 改进 4: 实现记忆系统
```python
# 中期实施：添加长期记忆
memory_system = MemorySystem(
    embedding_client=OpenAIEmbedding(),
    vector_db_client=PineconeClient()
)

# 存储偏好
await memory_system.add_episode(
    content="用户偏好使用 Vim 编辑器",
    memory_type=MemoryType.PREFERENCE
)
```

#### 改进 5: 添加成本追踪
```python
# 中期实施：添加成本追踪
billing_tracker = BillingTracker()

await billing_tracker.record_cost(
    model="deepseek-chat",
    input_tokens=1000,
    output_tokens=500
)
```

#### 改进 6: 实现状态机
```python
# 中期实施：添加状态机
state_machine = StateMachine()

# IDLE → PERCEIVING
await state_machine.transition(
    LoopState.PERCEIVING,
    event="loop_start"
)
```

### 5.3 长期改进（低优先级）

#### 改进 7: 开发可视化构建器
- 使用 React + React Flow
- 支持 Graph 可视化编辑
- 支持实时预览

#### 改进 8: 实现 Marketplace
- 模板共享
- 社区贡献
- 一键部署

---

## 六、对比总结

### 6.1 模块设计对比

| 模块 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| **执行引擎** | ExecutionProcessor + EventBus | AgentLoop | ✅ 添加状态机 + EventBus |
| **上下文管理** | ExecutionContext（详细） | LoopState（简单） | ✅ 增强字段，支持快照 |
| **工具系统** | Block（统一接口） | Tool（@tool 装饰器） | ✅ 统一接口规范 |
| **记忆系统** | Graphiti + ChatSession | MemoryManager（短期） | ✅ 双层记忆系统 |
| **成本追踪** | BillingTracker | 无 | ✅ 添加成本追踪 |
| **权限系统** | CopilotPermissions | 4 步管道 | ✅ 保持现有设计 |
| **审计日志** | 基础日志 | 8-phase JSONL | ✅ 保持现有设计 |
| **回滚机制** | 无 | 三级回滚 | ✅ 保持现有设计 |

### 6.2 数据结构对比

| 数据结构 | AutoGPT | OpsAgent | 借鉴建议 |
|----------|---------|----------|---------|
| **Graph** | Graph（图结构） | Prompt（文本） | ✅ 引入 Graph 定义 |
| **ExecutionContext** | 15+ 字段 | 3 字段 | ✅ 增强字段设计 |
| **BlockSchema** | Pydantic Model | 无 | ✅ 定义 Schema |
| **MemoryEpisode** | 向量 + 元数据 | 简单字符串 | ✅ 支持向量 + 元数据 |
| **CostRecord** | 详细成本记录 | 无 | ✅ 添加成本记录 |
| **ToolExecution** | 时间戳 + 性能 | 简单结果 | ✅ 增强执行记录 |

### 6.3 关键借鉴点总结

#### ⭐⭐⭐⭐⭐ 必须借鉴（5 个）

1. **状态机模式** - 清晰的状态管理
   ```python
   class LoopState(Enum):
       IDLE = "idle"
       PERCEIVING = "perceiving"
       REASONING = "reasoning"
       EXECUTING = "executing"
       COMPLETED = "completed"
       FAILED = "failed"
   ```

2. **事件驱动架构** - 模块解耦，实时监控
   ```python
   event_bus = EventBus()
   event_bus.subscribe(EventTypes.TOOL_CALL_COMPLETE, handler)
   await event_bus.emit(EventTypes.TOOL_CALL_COMPLETE, payload)
   ```

3. **增强的执行上下文** - 完整的信息追踪
   ```python
   @dataclass
   class LoopContext:
       loop_id: str
       session_id: str
       status: LoopStatus
       messages: List[BaseMessage]
       tool_executions: List[ToolExecution]
       snapshots: Dict[str, Dict]
       # ... 更多字段
   ```

4. **统一的工具接口** - 易于扩展和管理
   ```python
   class BaseTool(ABC):
       tool_id: str
       category: ToolCategory
       risk_level: ToolRiskLevel
       input_schema: Dict
       output_schema: Dict
       
       @abstractmethod
       async def execute(self, input_data, context):
           pass
   ```

5. **双层记忆系统** - 短期 + 长期记忆
   ```python
   class DualMemorySystem:
       def __init__(self):
           self._short_term = LoopContext()
           self._long_term = MemorySystem(...)
   ```

#### ⭐⭐⭐⭐ 强烈建议（4 个）

6. **成本追踪系统** - 成本透明化
   ```python
   billing_tracker = BillingTracker()
   await billing_tracker.record_cost(model, input_tokens, output_tokens)
   ```

7. **工具 Schema 定义** - 输入/输出验证
   ```python
   class ExecBashInput(BaseModel):
       command: str
       timeout: int = 30
   ```

8. **工具风险评估** - 动态风险等级
   ```python
   def get_risk_level(self, input_data) -> ToolRiskLevel:
       if "rm -rf" in input_data["command"]:
           return ToolRiskLevel.CRITICAL
   ```

9. **仓储模式** - 数据访问抽象
   ```python
   class ToolExecutionRepository:
       async def save_execution(self, execution):
           return await self._db.tool_execution.create(...)
   ```

#### ⭐⭐⭐ 建议借鉴（3 个）

10. **Graph 工作流定义** - 结构化任务
11. **策略模式** - 灵活的执行策略
12. **CompactionTracker** - 智能上下文压缩

---

### 6.4 实施优先级和时间表

#### 阶段 1: 基础增强（1-2 周）
- [ ] 实现状态机（StateMachine + LoopState）
- [ ] 添加 EventBus
- [ ] 增强 LoopContext（添加字段）
- [ ] 定义事件类型（EventTypes）

#### 阶段 2: 工具系统改造（2-3 周）
- [ ] 定义 BaseTool 基类
- [ ] 定义 ToolRiskLevel 枚举
- [ ] 定义 ToolCategory 枚举
- [ ] 重构现有工具（ExecBashTool, FileReadTool 等）
- [ ] 实现工具注册表（ToolRegistry）

#### 阶段 3: 高级功能（3-4 周）
- [ ] 实现记忆系统（MemorySystem）
- [ ] 集成向量数据库（如 ChromaDB）
- [ ] 实现双层记忆系统（DualMemorySystem）
- [ ] 实现成本追踪（BillingTracker）

#### 阶段 4: 优化和测试（2-3 周）
- [ ] 性能优化
- [ ] 单元测试
- [ ] 集成测试
- [ ] 文档更新

**总时间**: 8-12 周

---

### 6.5 OpsAgent 优势保持

以下功能是 OpsAgent 的优势，无需借鉴 AutoGPT：

✅ **10 环节安全管道**
- 注入检测 → LLM 推理 → 参数合法性检查 → SessionTrust 令牌检查
- IntentClassifier.classify_command() → PolicyEngine.evaluate()
- PreToolUse Hooks（4个Hook） → PrivilegeBroker.execute()
- PromptInjectionDetector.check_tool_output() → PostToolUse Hooks

✅ **最小权限执行**
```python
ops-reader: uid=9001  # 只读权限
ops-writer: uid=9002  # 有限写权限
```

✅ **三级回滚机制**
```python
L1 文件级: 从 .snapshots/ 恢复
L2 配置级: 恢复原始权限位
L3 服务级: systemctl start 恢复服务
```

✅ **8-phase 审计日志**
```json
{
  "phase": "complete",
  "receive": {"user_input": "..."},
  "perceive": {"disk": {...}},
  "reason": {"risk_level": "HIGH"},
  "validate": {"policy": "interactive"},
  "snapshot": {"snapshot_id": "snap_456"},
  "confirm": {"user_confirmed": true},
  "execute": {"exit_code": 0},
  "complete": {"output": "..."}
}
```

✅ **4 步权限管道**
```python
# Step 1: deny_rules - 绝对黑名单
# Step 2: mode_check - plan 模式拒绝写操作
# Step 3: allow_rules - 只读命令自动放行
# Step 4: ask_user - 其余操作询问用户
```

---

## 七、总结

### 7.1 核心差异

| 维度 | AutoGPT | OpsAgent | 建议方案 |
|------|---------|----------|---------|
| **安全性** | 中（Virus Scan + Moderation） | 高（10 环节安全管道） | 保持 OpsAgent |
| **灵活性** | 中（预定义 Graph） | 高（动态 LLM 决策） | 保持 OpsAgent |
| **可控性** | 高（可视化） | 中（Prompt 控制） | 引入可视化（可选） |
| **扩展性** | 高（Block 插件化） | 中（MCP + 自定义） | 引入 Block 架构 |
| **性能** | 高（分布式） | 中（单机） | 保持现有架构 |
| **用户体验** | 高（可视化 Builder） | 低（CLI） | 可选添加 Web UI |
| **记忆能力** | 强（Graphiti + ChatSession） | 弱（短期记忆） | ✅ 添加长期记忆 |
| **回滚能力** | 无 | 强（三级回滚） | 保持 OpsAgent |
| **审计能力** | 中 | 强（8-phase） | 保持 OpsAgent |
| **成本追踪** | 强 | 无 | ✅ 添加成本追踪 |
| **状态管理** | 清晰（状态机） | 模糊（简单变量） | ✅ 添加状态机 |
| **事件驱动** | 强（EventBus） | 弱（日志） | ✅ 添加 EventBus |

### 7.2 最终建议

**短期（1-2 个月）**:
1. ✅ 实现状态机模式
2. ✅ 添加 EventBus
3. ✅ 增强 LoopContext
4. ✅ 统一工具接口

**中期（3-6 个月）**:
5. ✅ 实现记忆系统
6. ✅ 添加成本追踪
7. ✅ 工具风险评估
8. ✅ 数据仓储模式

**长期（6-12 个月）**:
9. 🔧 开发可视化构建器（可选）
10. 🔧 实现 Marketplace（可选）
11. 🔧 分布式执行（可选）

**保持不变**:
- ✅ 10 环节安全管道
- ✅ 最小权限执行
- ✅ 三级回滚机制
- ✅ 8-phase 审计日志
- ✅ 4 步权限管道

---

**文档版本**: v1.0  
**最后更新**: 2026-04-21  
**作者**: Agent 搭建专家  
**建议实施周期**: 8-12 周
