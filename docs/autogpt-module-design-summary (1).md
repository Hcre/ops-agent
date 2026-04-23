# 🧩 AutoGPT 模块设计与关键数据结构深度分析
## OpsAgent 可借鉴的具体方案 - 核心总结

---

## 一、概述

本文档深度分析了 AutoGPT 的核心模块设计和关键数据结构，为 OpsAgent 提供具体的借鉴建议。

**分析重点**:
- 模块职责划分和设计模式
- 关键数据结构的设计原理
- 与 OpsAgent 的对比分析
- 具体的代码实现建议

---

## 二、核心模块设计对比

### 2.1 执行引擎

| 维度 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| **类名** | ExecutionProcessor | AgentLoop | ✅ 保持 OpsAgent |
| **事件机制** | EventBus | 无 | ✅ 添加 EventBus |
| **状态管理** | 状态枚举 | 简单变量 | ✅ 添加状态机 |
| **错误处理** | 完整的 try-catch | 基础异常 | ✅ 保持 OpsAgent |
| **审计追踪** | 事件日志 | 8-phase JSONL | ✅ 保持 OpsAgent |

**借鉴 1: 状态机模式**
```python
class LoopState(Enum):
    """Loop 状态枚举"""
    IDLE = "idle"
    PERCEIVING = "perceiving"
    REASONING = "reasoning"
    VALIDATING = "validating"
    EXECUTING = "executing"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class StateMachine:
    """状态机"""
    
    def __init__(self, initial_state: LoopState = LoopState.IDLE):
        self._current_state = initial_state
        self._history: List[StateTransition] = []
    
    async def transition(
        self,
        to_state: LoopState,
        event: str = "",
        metadata: Dict[str, Any] = None
    ):
        """执行状态转换"""
        from_state = self._current_state
        
        # 记录转换
        self._history.append({
            "from": from_state,
            "to": to_state,
            "event": event,
            "timestamp": datetime.utcnow(),
            "metadata": metadata or {}
        })
        
        # 触发回调
        handler = self._handlers.get((from_state, to_state))
        if handler:
            await handler(metadata)
        
        self._current_state = to_state
    
    def get_current_state(self) -> LoopState:
        return self._current_state
```

**借鉴 2: 事件总线**
```python
class EventBus:
    """事件总线"""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._history: List[Dict] = []
    
    def subscribe(self, event_type: str, handler: Callable):
        """订阅事件"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    
    async def emit(
        self,
        event_type: str,
        payload: Dict[str, Any]
    ):
        """发布事件"""
        event = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.utcnow(),
        }
        
        self._history.append(event)
        
        # 通知订阅者
        if event_type in self._subscribers:
            tasks = [
                asyncio.create_task(handler(event))
                for handler in self._subscribers[event_type]
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

# 定义事件类型
class EventTypes:
    LOOP_START = "loop.start"
    LOOP_COMPLETE = "loop.complete"
    TOOL_CALL_START = "tool_call.start"
    TOOL_CALL_COMPLETE = "tool_call.complete"
    PERMISSION_CHECK = "permission.check"
    AUDIT_LOG = "audit.log"

# 使用示例
event_bus = EventBus()

# 订阅审计事件
async def audit_handler(event: Dict):
    await db_manager.audit_log.create({
        "event_type": event["type"],
        "payload": event["payload"],
        "timestamp": event["timestamp"],
    })

event_bus.subscribe(EventTypes.TOOL_CALL_COMPLETE, audit_handler)
```

---

### 2.2 执行上下文

| 字段 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| loop_id | ✓ | ✗ | ✅ 添加 |
| session_id | ✓ | ✗ | ✅ 添加 |
| user_id | ✓ | ✗ | ✅ 添加 |
| status | ✓ | ✗ | ✅ 添加（LoopStatus） |
| messages | ✓ | ✓ | ✅ 保持 |
| tool_executions | ✓ | ✗ | ✅ 添加 |
| snapshots | ✓ | ✗ | ✅ 添加 |
| workspace_path | ✓ | ✗ | ✅ 添加 |
| cache | ✓ | ✗ | ✅ 添加 |
| permissions | ✓ | ✗ | ✅ 添加 |
| started_at | ✓ | ✗ | ✅ 添加 |
| completed_at | ✓ | ✗ | ✅ 添加 |
| total_llm_calls | ✓ | ✗ | ✅ 添加 |
| total_tool_calls | ✓ | ✗ | ✅ 添加 |
| total_tokens | ✓ | ✗ | ✅ 添加 |

**借鉴 3: 增强的 LoopContext**
```python
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
    
    # 时间信息
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # 性能指标
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    
    def add_message(self, message: BaseMessage):
        """添加消息（滑动窗口）"""
        self.messages.append(message)
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
```

---

### 2.3 工具系统

| 特性 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| **基类** | BaseBlock | @tool 装饰器 | ✅ 统一接口 |
| **Schema** | Pydantic Model | 无 | ✅ 添加 |
| **分类** | ToolCategory | 无 | ✅ 添加 |
| **风险等级** | ToolRiskLevel | 无 | ✅ 添加 |
| **输入验证** | validate_input() | 无 | ✅ 添加 |
| **输出验证** | validate_output() | 无 | ✅ 添加 |
| **元数据** | metadata | 无 | ✅ 添加 |

**借鉴 4: 统一工具接口**
```python
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
    
    @staticmethod
    def get_schema() -> Dict[str, Any]:
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
    
    def get_risk_level(
        self,
        input_data: Dict[str, Any]
    ) -> ToolRiskLevel:
        """评估风险等级（可重写）"""
        return self.risk_level

# 示例：ExecBashTool
class ExecBashTool(BaseTool):
    """执行 Bash 命令工具"""
    
    tool_id = "exec_bash"
    name = "Execute Bash Command"
    description = "执行 Bash 命令并返回结果"
    category = ToolCategory.SYSTEM
    risk_level = ToolRiskLevel.HIGH
    tags = ["bash", "command", "system"]
    required_permissions = ["bash.execute"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }
    }
    
    def __init__(self):
        self._privilege_broker = PrivilegeBroker()
    
    def get_risk_level(
        self,
        input_data: Dict[str, Any]
    ) -> ToolRiskLevel:
        """评估风险等级"""
        command = input_data["command"]
        
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
        
        command = input_data["command"]
        timeout = input_data.get("timeout", 30)
        
        # 评估风险
        risk_level = self.get_risk_level(input_data)
        
        # 最小权限执行
        result = await self._privilege_broker.execute_command(
            command=command,
            risk_level=risk_level.value,
            timeout=timeout
        )
        
        return {
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "exit_code": result.returncode,
        }

# 工具注册表
class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool):
        """注册工具"""
        self._tools[tool.tool_id] = tool
    
    def get(self, tool_id: str) -> Optional[BaseTool]:
        """获取工具"""
        return self._tools.get(tool_id)
    
    def list_all(self) -> List[Dict[str, Any]]:
        """列出所有工具"""
        return [tool.get_schema() for tool in self._tools.values()]

# 全局工具注册表
tool_registry = ToolRegistry()
tool_registry.register(ExecBashTool())
```

---

### 2.4 记忆系统

| 特性 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| **短期记忆** | ChatSession | MemoryManager | ✅ 保持 |
| **长期记忆** | Graphiti | 无 | ✅ 添加 |
| **向量搜索** | ✓ | ✗ | ✅ 添加 |
| **记忆分类** | MemoryType | 无 | ✅ 添加 |
| **元数据** | ✓ | 无 | ✅ 添加 |

**借鉴 5: 双层记忆系统**
```python
class MemoryType(Enum):
    """记忆类型"""
    FACT = "fact"
    PREFERENCE = "preference"
    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"

@dataclass
class MemoryEpisode:
    """记忆片段"""
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    memory_type: MemoryType = MemoryType.FACT
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    importance_score: float = 0.5
    access_count: int = 0

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
        
        # 存储到向量数据库
        episode = MemoryEpisode(
            content=content,
            memory_type=memory_type,
            embedding=embedding,
            metadata=metadata or {},
        )
        
        await self._vector_db_client.insert(
            episode.model_dump()
        )
        
        return episode.episode_id
    
    async def search(
        self,
        query: str,
        top_k: int = 5,
        memory_type: Optional[MemoryType] = None
    ) -> List[MemoryEpisode]:
        """搜索记忆"""
        
        # 生成查询嵌入
        query_embedding = await self._embedding_client.embed(query)
        
        # 向量搜索
        results = await self._vector_db_client.search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters={"memory_type": memory_type.value} if memory_type else None,
        )
        
        return [MemoryEpisode(**r) for r in results]

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
    
    async def get_context_with_memory(
        self,
        query: str = "",
        include_long_term: bool = True
    ) -> List[BaseMessage]:
        """获取包含记忆的上下文"""
        
        context = self._short_term.messages.copy()
        
        # 搜索长期记忆
        if include_long_term and query:
            episodes = await self._long_term.search(query, top_k=3)
            
            for episode in episodes:
                memory_msg = SystemMessage(
                    content=f"[记忆] {episode.content} (类型: {episode.memory_type.value})"
                )
                context.append(memory_msg)
        
        return context
```

---

### 2.5 成本追踪

| 特性 | AutoGPT | OpsAgent | 借鉴建议 |
|------|---------|----------|---------|
| **成本追踪** | BillingTracker | 无 | ✅ 添加 |
| **Token 统计** | ✓ | ✗ | ✅ 添加 |
| **按模型统计** | ✓ | ✗ | ✅ 添加 |
| **按会话统计** | ✓ | ✗ | ✅ 添加 |
| **成本报告** | ✓ | ✗ | ✅ 添加 |

**借鉴 6: 成本追踪系统**
```python
# 模型定价
MODEL_PRICING = {
    "gpt-4": {
        "input_cost": 0.03,      # $/1K tokens
        "output_cost": 0.06,     # $/1K tokens
    },
    "gpt-3.5-turbo": {
        "input_cost": 0.0005,
        "output_cost": 0.0015,
    },
    "deepseek-chat": {
        "input_cost": 0.00014,
        "output_cost": 0.00028,
    },
}

@dataclass
class CostRecord:
    """成本记录"""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    execution_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @classmethod
    def create(
        cls,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: str = "",
        execution_id: str = ""
    ) -> "CostRecord":
        """创建成本记录"""
        
        pricing = MODEL_PRICING.get(model, {})
        input_cost = input_tokens * pricing.get("input_cost", 0) / 1000
        output_cost = output_tokens * pricing.get("output_cost", 0) / 1000
        
        return cls(
            session_id=session_id,
            execution_id=execution_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
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
        session_id: str = ""
    ):
        """记录成本"""
        record = CostRecord.create(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=session_id
        )
        self._cost_records.append(record)
        logger.info(
            f"Cost: {model} - {record.total_tokens} tokens - ¥{record.total_cost:.4f}"
        )
    
    def get_total_cost(self, session_id: str = "") -> float:
        """获取总成本"""
        if not session_id:
            return sum(r.total_cost for r in self._cost_records)
        else:
            return sum(
                r.total_cost
                for r in self._cost_records
                if r.session_id == session_id
            )
    
    def generate_report(self, session_id: str = "") -> str:
        """生成成本报告"""
        
        total_cost = self.get_total_cost(session_id)
        
        lines = [
            "=" * 60,
            "成本追踪报告",
            "=" * 60,
            f"总成本: ¥{total_cost:.4f}",
            "-" * 60,
            "按模型统计:",
            "-" * 60,
        ]
        
        # 按模型统计
        model_stats: Dict[str, Dict] = {}
        for record in self._cost_records:
            if record.model not in model_stats:
                model_stats[record.model] = {
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "call_count": 0,
                }
            
            model_stats[record.model]["total_cost"] += record.total_cost
            model_stats[record.model]["total_tokens"] += record.total_tokens
            model_stats[record.model]["call_count"] += 1
        
        for model, stats in model_stats.items():
            lines.append(f"  {model}:")
            lines.append(f"    成本: ¥{stats['total_cost']:.4f}")
            lines.append(f"    Tokens: {stats['total_tokens']:,}")
            lines.append(f"    调用次数: {stats['call_count']}")
        
        return "\n".join(lines)

# 在 Agent Loop 中使用
billing_tracker = BillingTracker()

async def generate_with_tracking(
    llm,
    messages: List[BaseMessage],
    session_id: str
) -> BaseMessage:
    """带成本追踪的 LLM 生成"""
    
    response = await llm.ainvoke(messages)
    
    # 记录成本
    await billing_tracker.record_cost(
        model=llm.model_name,
        input_tokens=response.usage_metadata["input_tokens"],
        output_tokens=response.usage_metadata["output_tokens"],
        session_id=session_id
    )
    
    return response
```

---

## 三、关键数据结构对比

| 数据结构 | AutoGPT 字段数 | OpsAgent 字段数 | 借鉴建议 |
|----------|---------------|----------------|---------|
| **ExecutionContext** | 15+ | 3 | ✅ 增强到 12+ |
| **BlockSchema** | 8 | 0 | ✅ 添加 |
| **MemoryEpisode** | 8 | 1 | ✅ 添加 |
| **CostRecord** | 12 | 0 | ✅ 添加 |
| **ToolExecution** | 6 | 0 | ✅ 添加 |

---

## 四、设计模式借鉴

### 4.1 状态机模式（State Machine）
- **应用**: Loop 状态管理
- **优势**: 状态转换清晰，易于调试
- **实现**: `StateMachine` + `LoopState` 枚举

### 4.2 观察者模式（Observer）
- **应用**: 事件驱动架构
- **优势**: 模块解耦，易于扩展
- **实现**: `EventBus` + 事件订阅

### 4.3 策略模式（Strategy）
- **应用**: 工具风险等级评估
- **优势**: 灵活的策略切换
- **实现**: `get_risk_level()` 方法

### 4.4 仓储模式（Repository）
- **应用**: 数据访问层抽象
- **优势**: 数据存储解耦
- **实现**: `ToolExecutionRepository`

---

## 五、实施建议

### 5.1 立即可实施（高优先级）

#### 1. 添加状态机
```python
# 在 src/agents/agent.py 中添加
class StateMachine:
    def __init__(self):
        self._current_state = LoopState.IDLE
        self._history = []
    
    async def transition(self, to_state, event="", metadata=None):
        # 记录状态转换
        pass
```

#### 2. 添加 EventBus
```python
# 在 src/utils/ 下创建 event_bus.py
event_bus = EventBus()

# 订阅审计事件
event_bus.subscribe(EventTypes.TOOL_CALL_COMPLETE, audit_handler)
```

#### 3. 增强 LoopContext
```python
# 在 src/agents/agent.py 中增强 LoopContext
@dataclass
class LoopContext:
    loop_id: str
    session_id: str
    status: LoopStatus
    tool_executions: List[ToolExecution]
    # ... 其他字段
```

#### 4. 统一工具接口
```python
# 在 src/tools/ 下创建 base_tool.py
class BaseTool(ABC):
    tool_id: str
    category: ToolCategory
    risk_level: ToolRiskLevel
    
    @abstractmethod
    async def execute(self, input_data, context):
        pass
```

### 5.2 中期实施（中优先级）

#### 5. 实现记忆系统
```python
# 集成向量数据库（如 ChromaDB）
memory_system = MemorySystem(
    embedding_client=OpenAIEmbedding(),
    vector_db_client=ChromaClient()
)
```

#### 6. 添加成本追踪
```python
# 记录 LLM 成本
billing_tracker = BillingTracker()
await billing_tracker.record_cost(...)
```

### 5.3 长期实施（低优先级）

#### 7. 开发可视化构建器
- 使用 React + React Flow
- 支持 Graph 可视化编辑

#### 8. 实现 Marketplace
- 模板共享
- 社区贡献

---

## 六、总结

### 6.1 核心借鉴点（按优先级排序）

#### ⭐⭐⭐⭐⭐ 必须借鉴（5 个）

1. **状态机模式** - 清晰的状态管理
2. **事件驱动架构** - 模块解耦，实时监控
3. **增强的执行上下文** - 完整的信息追踪
4. **统一的工具接口** - 易于扩展和管理
5. **双层记忆系统** - 短期 + 长期记忆

#### ⭐⭐⭐⭐ 强烈建议（3 个）

6. **成本追踪系统** - 成本透明化
7. **工具 Schema 定义** - 输入/输出验证
8. **工具风险评估** - 动态风险等级

#### ⭐⭐⭐ 建议借鉴（2 个）

9. **仓储模式** - 数据访问抽象
10. **工具注册表** - 统一工具管理

### 6.2 OpsAgent 优势保持（无需借鉴）

- ✅ 10 环节安全管道
- ✅ 最小权限执行
- ✅ 三级回滚机制
- ✅ 8-phase 审计日志
- ✅ 4 步权限管道

---

**最后更新**: 2026-04-21  
**文档版本**: v1.0  
**建议实施周期**: 3-6 个月
