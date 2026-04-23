# OpsAgent 重构方案（待实现）

> 来源：AutoGPT 架构分析文档借鉴 + 当前代码痛点总结  
> 状态：设计完成，等待实现

---

## 一、统一工具接口（BaseTool）

**价值**：工具自己声明风险等级，替换 `PermissionManager._classify_risk()` 的前缀匹配猜测。

**核心设计**：

```python
class ToolRiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ToolCategory(Enum):
    FILE = "file"
    SYSTEM = "system"
    NETWORK = "network"

class BaseTool(ABC):
    tool_id: str
    name: str
    description: str
    category: ToolCategory
    risk_level: ToolRiskLevel  # 静态默认值
    cmd_type: Literal["read", "file", "service"]  # 告诉 broker 用哪个账号

    input_schema: dict   # Pydantic model_json_schema()
    output_schema: dict

    def get_risk_level(self, input_data: dict) -> ToolRiskLevel:
        """动态评估风险，子类可重写。默认返回静态 risk_level。"""
        return self.risk_level

    @abstractmethod
    async def execute(self, input_data: dict, context) -> dict:
        ...
```

**关键规则**：
- 封装工具（`restart_service`、`delete_file`）用静态 `risk_level`，不重写
- `exec_bash` 必须重写 `get_risk_level()`，动态分析命令内容
- `PermissionManager` 优先读 `tool.get_risk_level(args)`，无 `BaseTool` 的工具走现有前缀匹配兜底

**迁移顺序**：感知工具（风险静态）→ `exec_bash`（需重写动态判断）

---

## 二、LoopState 增强（运行时追踪字段）

**价值**：记录工具调用历史，支持去重检测和性能分析。

```python
@dataclass
class ToolExecution:
    tool_name: str
    tool_args: dict
    result: ToolResult
    started_at: float
    elapsed_ms: float

# 在现有 LoopState 上新增：
tool_executions: list[ToolExecution] = field(default_factory=list)
total_llm_calls: int = 0
total_tool_calls: int = 0
started_at: float = field(default_factory=time.monotonic)
```

**注意**：`ToolExecution` 直接复用现有 `ToolResult`，不新建 dataclass。

---

## 三、状态机重构主循环

**价值**：状态转换可追踪，替换现有 `transition_reason` 字符串。

```python
class LoopPhase(Enum):
    IDLE = "idle"
    PERCEIVING = "perceiving"
    REASONING = "reasoning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"

# LoopState 新增：
phase: LoopPhase = LoopPhase.IDLE
phase_history: list[dict] = field(default_factory=list)

def enter_phase(self, phase: LoopPhase, metadata: dict = None) -> None:
    self.phase_history.append({
        "from": self.phase.value,
        "to": phase.value,
        "turn": self.turn_count,
        "ts": time.time(),
        **(metadata or {}),
    })
    self.phase = phase
```

**前提**：等 Week 7-8 核心功能稳定后再做，现在改动收益低。

---

## 四、命令去重缓存

**价值**：防止 LLM 在推理循环里重复执行相同命令，减少 API 调用。

```python
class CommandDeduplicator:
    def __init__(self, window_seconds: int = 60):
        self._cache: dict[str, float] = {}  # cmd_hash -> timestamp
        self._window = window_seconds

    def is_duplicate(self, cmd: str) -> bool:
        h = hashlib.sha256(cmd.encode()).hexdigest()
        if h in self._cache:
            if time.time() - self._cache[h] < self._window:
                return True
        self._cache[h] = time.time()
        return False
```

集成到 `_handle_single_tool()` 的工具执行前检查。

---

## 五、感知层 TTL 缓存

**价值**：`PerceptionAggregator` 现在每次都重新采集，加缓存减少重复系统调用。

```python
# aggregator.py
_snapshot_cache: tuple[float, PerceptionResult] | None = None
_cache_ttl: float = 30.0  # 30 秒内复用

async def snapshot(self) -> PerceptionResult:
    if self._snapshot_cache:
        ts, result = self._snapshot_cache
        if time.monotonic() - ts < self._cache_ttl:
            return result
    result = await self._collect()
    self._snapshot_cache = (time.monotonic(), result)
    return result
```

---

## 实施优先级

| 方案 | 优先级 | 前提条件 | 预计周次 |
|------|--------|---------|---------|
| 感知层 TTL 缓存 | 高 | 无 | 随时可做 |
| LoopState 追踪字段 | 高 | 无 | Week 4 末 |
| 命令去重缓存 | 中 | LoopState 追踪字段 | Week 5 |
| 统一工具接口 BaseTool | 中 | Week 4 工具层稳定 | Week 5-6 |
| 状态机重构主循环 | 低 | 核心功能全部稳定 | Week 8+ |
