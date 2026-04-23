# 🔍 OpsAgent vs Keep 深度对比分析与优化建议

## 一、OpsAgent 可以借鉴 Keep 的地方

### 1️⃣ **插件化架构（最重要）**

#### Keep 的优势
```python
# Keep: BaseProvider 统一接口，131+ Provider
class BaseProvider(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def _get_alerts_fingerprint(self):  # 指纹计算
        pass

    @abc.abstractmethod
    def get_alerts(self):  # 获取告警
        pass

    @abc.abstractmethod
    def validate_webhook(self):  # Webhook 验证
        pass
```

#### OpsAgent 的现状
```python
# OpsAgent: MCP 支持，但工具较少
# 工具注册分散在多个模块
# 缺乏统一的 Provider 接口
```

#### 建议
```python
# 1. 定义统一的 Provider 接口
class BaseProvider(metaclass=abc.ABCMeta):
    PROVIDER_CATEGORY: Literal["Monitoring", "Ticketing", "Messaging"]
    PROVIDER_SCOPES: list[ProviderScope]

    @abc.abstractmethod
    def execute(self, params: dict) -> ToolResult:
        """执行操作"""
        pass

    @abc.abstractmethod
    def validate_params(self, params: dict) -> bool:
        """参数验证"""
        pass

    @abc.abstractmethod
    def get_risk_level(self, params: dict) -> str:
        """返回操作风险等级"""
        pass

# 2. 实现 Provider Registry
class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider):
        self._providers[provider.__class__.__name__] = provider

    def get_provider(self, name: str) -> BaseProvider:
        return self._providers.get(name)

    def list_providers(self, category: str = None):
        if category:
            return [p for p in self._providers.values()
                   if p.PROVIDER_CATEGORY == category]
        return self._providers.values()

# 3. 实现 Provider 工厂
class ProviderFactory:
    @staticmethod
    def create_provider(config: ProviderConfig) -> BaseProvider:
        """根据配置动态创建 Provider"""
        provider_type = config.provider_type
        provider_class = PROVIDER_MAP[provider_type]
        return provider_class(config)
```

**预期收益**：
- 🔌 工具扩展性提升 10 倍
- 📦 社区贡献更便捷
- 🔄 统一的风险评估

---

### 2️⃣ **工作流编排引擎**

#### Keep 的优势
```python
# Keep: Workflow 灵活编排
class Workflow:
    workflow_steps: List[Step]      # 数据处理步骤
    workflow_actions: List[Step]    # 动作执行步骤
    workflow_strategy: WorkflowStrategy  # 执行策略

    class WorkflowStrategy(Enum):
        NONPARALLEL = "nonparallel"
        NONPARALLEL_WITH_RETRY = "nonparallel_with_retry"
        PARALLEL = "parallel"

    def run_steps(self):
        for step in self.workflow_steps:
            try:
                step_ran = step.run()
                if step_ran and not step.continue_to_next_step:
                    break  # 可控制流程
            except StepError as e:
                raise

    def run_actions(self):
        for action in self.workflow_actions:
            action_ran, action_error, action_stop = self.run_action(action)
            if action_stop:
                break
```

#### OpsAgent 的现状
```python
# OpsAgent: 简单的 LLM 调用循环
class AgentLoop:
    async def run(self):
        while turn_count < max_turns:
            # LLM 推理
            response = await self._client.chat.completions.create(...)

            # 工具执行
            for tool_call in response.tool_calls:
                result = await self._execute_tool(tool_call)

            # 简单的状态转换
            self.state.transition_reason = "tool_result_continuation"
```

#### 建议
```python
# 1. 引入 Workflow 概念
class Workflow:
    """运维工作流"""
    workflow_id: str
    triggers: List[Trigger]      # 触发器（告警/定时/手动）
    steps: List[Step]            # 处理步骤（过滤、计算、验证）
    actions: List[Action]        # 执行动作（通知、执行、记录）
    strategy: WorkflowStrategy   # 执行策略

    def execute(self, context: ExecutionContext):
        """执行工作流"""
        # 步骤阶段
        for step in self.steps:
            if not step.should_run(context):
                continue

            result = step.execute(context)
            context.update(result)

            if result.stop_workflow:
                break

        # 动作阶段
        for action in self.actions:
            action_result = action.execute(context)

            if action_result.error and self.strategy == WorkflowStrategy.NONPARALLEL:
                break

# 2. 内置常用工作流模板
WORKFLOW_TEMPLATES = {
    "disk_cleanup": Workflow(
        triggers=[CronTrigger("0 */6 * * *")],
        steps=[
            DiskThresholdCheck(threshold=85),
            IdentifyTargetDirectories(),
            CalculateCleanupSize(),
        ],
        actions=[
            SnapshotBeforeAction(),
            ExecuteCleanup(),
            NotifyTeam(),
        ],
        strategy=WorkflowStrategy.NONPARALLEL_WITH_RETRY,
    ),

    "service_restart": Workflow(
        triggers=[AlertTrigger("service_down")],
        steps=[
            IdentifyService(),
            CheckServiceHealth(),
            GetRecentLogs(),
        ],
        actions=[
            CreateIncident(),
            AttemptRestart(),
            VerifyRecovery(),
            CloseIncident(),
        ],
        strategy=WorkflowStrategy.PARALLEL,
    ),
}
```

**预期收益**：
- 🎯 复杂场景处理能力提升
- 📋 工作流可复用、可共享
- 🔄 支持并行执行、重试策略

---

### 3️⃣ **去重机制**

#### Keep 的优势
```python
# Keep: 指纹去重
class AlertDeduplicator:
    def _apply_deduplication_rule(self, alert: AlertDto, rule: DeduplicationRuleDto):
        # 1. 移除忽略字段
        alert_copy = copy.deepcopy(alert)
        for field in rule.ignore_fields:
            alert_copy = self._remove_field(field, alert_copy)

        # 2. 计算 hash
        alert_hash = hashlib.sha256(
            json.dumps(alert_copy.dict(), default=str, sort_keys=True).encode()
        ).hexdigest()

        # 3. 检查是否重复
        last_hash = get_last_alert_hash(alert.fingerprint)
        if last_hash == alert_hash:
            alert.isFullDuplicate = True
        elif last_hash:
            alert.isPartialDuplicate = True
```

#### OpsAgent 的现状
```python
# OpsAgent: 没有去重机制
# 每次用户输入都会触发完整的推理链
```

#### 建议
```python
# 1. 命令去重
class CommandDeduplicator:
    """命令去重器"""

    def __init__(self):
        self._recent_commands: dict[str, float] = {}  # cmd_hash -> timestamp

    def is_duplicate(self, cmd: str, window: int = 300) -> bool:
        """检查是否在时间窗口内重复"""
        cmd_hash = hashlib.sha256(cmd.encode()).hexdigest()

        if cmd_hash in self._recent_commands:
            elapsed = time.time() - self._recent_commands[cmd_hash]
            if elapsed < window:
                return True

        self._recent_commands[cmd_hash] = time.time()
        return False

# 2. 意图去重
class IntentDeduplicator:
    """意图去重器"""

    def __init__(self):
        self._recent_intents: dict[str, float] = {}

    def should_skip(self, intent: str, params: dict, window: int = 60) -> bool:
        """检查相同意图是否在短时间内重复执行"""
        intent_key = f"{intent}:{json.dumps(params, sort_keys=True)}"

        if intent_key in self._recent_intents:
            elapsed = time.time() - self._recent_intents[intent_key]
            if elapsed < window:
                return True

        self._recent_intents[intent_key] = time.time()
        return False

# 3. 集成到 AgentLoop
class AgentLoop:
    def __init__(self):
        self._cmd_dedup = CommandDeduplicator()
        self._intent_dedup = IntentDeduplicator()

    async def run(self):
        # ... LLM 推理 ...

        # 检查去重
        for tool_call in response.tool_calls:
            cmd = tool_call.function.arguments.get("cmd", "")

            if self._cmd_dedup.is_duplicate(cmd):
                # 返回缓存结果
                result = self._get_cached_result(cmd)
                continue

            # 执行命令
            result = await self._execute_tool(tool_call)
```

**预期收益**：
- ⚡ 减少 30-50% 的重复计算
- 💰 降低 LLM API 调用成本
- 🚀 提升响应速度

---

### 4️⃣ **事件关联与聚类**

#### Keep 的优势
```python
# Keep: AI 事件关联
class IncidentBl:
    async def cluster_incidents_with_ai(self):
        alerts = get_recent_alerts()

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{
                "role": "user",
                "content": f"分析这些告警是否属于同一个事件: {alerts}"
            }],
            response_format={"type": "json_schema"}
        )

        # 自动创建事件
        incident = create_incident(analysis_result)
```

#### OpsAgent 的现状
```python
# OpsAgent: 任务系统，但没有智能关联
class TaskManager:
    # 任务独立管理，没有关联逻辑
    pass
```

#### 建议
```python
# 1. 事件关联器
class IncidentCorrelator:
    """事件关联器"""

    def __init__(self):
        self._embeddings: dict[str, np.ndarray] = {}  # 任务嵌入向量

    def correlate(self, tasks: List[Task]) -> List[Incident]:
        """关联任务到事件"""
        # 1. 生成任务嵌入
        embeddings = self._generate_embeddings(tasks)

        # 2. 计算相似度
        similarity_matrix = self._compute_similarity(embeddings)

        # 3. 聚类
        clusters = self._cluster_tasks(similarity_matrix, threshold=0.8)

        # 4. 创建事件
        incidents = []
        for cluster in clusters:
            incident = Incident(
                tasks=cluster.tasks,
                correlation_score=cluster.score,
                ai_summary=self._generate_summary(cluster),
            )
            incidents.append(incident)

        return incidents

    def _generate_embeddings(self, tasks: List[Task]) -> np.ndarray:
        """使用 LLM 生成任务嵌入向量"""
        embeddings = []
        for task in tasks:
            response = llm.embed(
                text=f"{task.title} {task.risk_level}"
            )
            embeddings.append(response)
        return np.array(embeddings)

# 2. 事件建议系统
class IncidentSuggestionBl:
    """事件建议系统"""

    def suggest_correlation(self, task: Task) -> IncidentSuggestion:
        """建议关联到现有事件或创建新事件"""
        # 1. 查找相似事件
        similar_incidents = self._find_similar_incidents(task)

        # 2. AI 分析
        analysis = llm.analyze(
            prompt=f"""
            任务: {task.title}
            相似事件: {[i.name for i in similar_incidents]}

            建议是否关联到现有事件，并给出理由。
            """,
            response_format={
                "should_correlate": bool,
                "incident_id": str | None,
                "reason": str,
            }
        )

        return IncidentSuggestion(
            should_correlate=analysis["should_correlate"],
            incident_id=analysis["incident_id"],
            reason=analysis["reason"],
        )
```

**预期收益**：
- 🔍 自动发现相关性问题
- 📊 问题根因分析更准确
- 🤖 减少人工关联工作量

---

### 5️⃣ **多租户隔离**

#### Keep 的优势
```python
# Keep: 完整的多租户隔离
class Alert:
    tenant_id: str = Field(foreign_key="tenant.id")

class Incident:
    tenant_id: str = Field(foreign_key="tenant.id")

# 每个租户独立配置、独立记忆
```

#### OpsAgent 的现状
```python
# OpsAgent: 单用户设计
# 没有多租户支持
```

#### 建议
```python
# 1. 引入租户概念
class Tenant:
    """租户"""
    tenant_id: str
    name: str
    config: TenantConfig
    permissions: List[str]

class TenantConfig:
    """租户配置"""
    max_risk_level: str = "HIGH"
    allowed_commands: List[str] = []
    denied_commands: List[str] = []
    custom_providers: List[str] = []

# 2. 租户隔离
class TenantContext:
    """租户上下文"""
    tenant_id: str
    config: TenantConfig
    memory: TenantMemory
    audit_log: TenantAuditLog

# 3. 集成到 AgentLoop
class AgentLoop:
    def __init__(self, config: AgentConfig, tenant_context: TenantContext):
        self._tenant = tenant_context
        self._perm_mgr = PermissionManager(config, tenant_context.config)

    async def run(self):
        # 每个操作都检查租户权限
        for tool_call in response.tool_calls:
            result = await self._execute_with_tenant_check(
                tool_call,
                self._tenant
            )
```

**预期收益**：
- 🏢 支持 SaaS 多租户部署
- 🔐 租户间完全隔离
- ⚙️ 租户级自定义配置

---

### 6️⃣ **Web UI 前端**

#### Keep 的优势
```typescript
// Keep: keep-ui 完整的前端界面
// - 告警仪表盘
// - 事件管理
// - 工作流编辑器
// - 实时状态
```

#### OpsAgent 的现状
```bash
# OpsAgent: 仅 CLI
# 没有可视化界面
```

#### 建议
```typescript
// 1. 技术栈
// - React + TypeScript
// - WebSocket 实时通信
// - Ant Design 组件库

// 2. 核心页面
// - 仪表盘：任务状态、系统健康度、告警统计
// - 任务列表：任务详情、执行日志、关联事件
// - 工作流编辑器：可视化编排工作流
// - 安全审计：操作历史、风险统计、权限管理
// - 系统感知：实时磁盘、进程、网络监控

// 3. 实时通信
// WebSocket 接口
// - 实时任务状态更新
// - 实时系统指标
// - 实时告警推送

// 4. 集成方案
// FastAPI 后端 + React 前端
// REST API + WebSocket 双通道
```

**预期收益**：
- 🖥️ 用户体验大幅提升
- 📊 可视化数据分析
- 🔄 实时监控

---

### 7️⃣ **AI 摘要与报告生成**

#### Keep 的优势
```python
# Keep: AI 生成事件摘要
async def __generate_summary(self, incident_id, incident):
    if fingerprints_count > 5 and not incident.user_summary:
        # 异步生成摘要
        await pool.enqueue_job(
            "process_summary_generation",
            incident_id=incident_id
        )

# AI 报告分析
class IncidentReportsBl:
    def __calculate_report_in_openai(self, incidents):
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{
                "role": "user",
                "content": json.dumps(incidents_minified)
            }],
            response_format={
                "type": "json_schema",
                "json_schema": IncidentReport.schema()
            }
        )
        return OpenAIReportPart(**json.loads(response))
```

#### OpsAgent 的现状
```python
# OpsAgent: 没有自动摘要和报告
```

#### 建议
```python
# 1. 任务摘要生成
class TaskSummarizer:
    """任务摘要生成器"""

    async def generate_summary(self, task: Task) -> str:
        """生成任务摘要"""
        # 收集上下文
        context = self._collect_task_context(task)

        # LLM 生成摘要
        summary = await llm.generate(
            prompt=f"""
            任务: {task.title}
            状态: {task.status}
            执行日志: {task.logs}

            生成简洁的任务摘要（100字以内）。
            """,
            max_tokens=200
        )

        return summary

# 2. 系统健康报告
class SystemReportGenerator:
    """系统报告生成器"""

    async def generate_daily_report(self) -> SystemReport:
        """生成每日系统报告"""
        # 1. 收集数据
        tasks = self._get_completed_tasks(last_hours=24)
        incidents = self._get_incidents(last_hours=24)
        metrics = self._get_system_metrics()

        # 2. LLM 分析
        report = await llm.generate(
            prompt=f"""
            任务执行情况: {tasks}
            事件发生情况: {incidents}
            系统指标: {metrics}

            生成每日系统健康报告，包括：
            1. 任务执行统计
            2. 事件总结
            3. 风险提示
            4. 改进建议
            """,
            response_format=SystemReport.schema()
        )

        return report

# 3. 集成到任务系统
class Task:
    async def complete(self):
        """任务完成时自动生成摘要"""
        self.summary = await TaskSummarizer().generate_summary(self)
        self.save()
```

**预期收益**：
- 📝 自动生成运维报告
- 💡 智能改进建议
- 📊 数据驱动决策

---

### 8️⃣ **多模型支持与成本优化**

#### Keep 的优势
```python
# Keep: 多模型路由
MODEL_PROFILES = {
    "haiku": {
        "model_id": "haiku",
        "context_limit": 200000,
        "cost_per_1k_tokens": 0.00025,
    },
    "sonnet": {
        "model_id": "sonnet",
        "context_limit": 200000,
        "cost_per_1k_tokens": 0.003,
    },
}

# 路由策略
class ModelRouter:
    def route(self, task_type: str) -> str:
        if task_type in ["read", "search"]:
            return "haiku"  # 廉价模型
        elif task_type in ["reasoning", "planning"]:
            return "sonnet"  # 强力模型
```

#### OpsAgent 的现状
```python
# OpsAgent: 单一模型
MODEL_PROFILES = {
    "deepseek-r1": {
        "model_id": "deepseek-chat",
        "context_limit": 64000,
    }
}
```

#### 建议
```python
# 1. 扩展多模型支持
MODEL_PROFILES = {
    "deepseek-chat": {
        "model_id": "deepseek-chat",
        "context_limit": 64000,
        "cost_per_1k_tokens": 0.001,
        "supports_tools": True,
        "supports_thinking": False,
    },
    "deepseek-reasoner": {
        "model_id": "deepseek-reasoner",
        "context_limit": 64000,
        "cost_per_1k_tokens": 0.005,
        "supports_tools": False,
        "supports_thinking": True,
    },
    "qwen3-8b": {
        "model_id": "qwen3-8b",
        "context_limit": 32000,
        "cost_per_1k_tokens": 0.0005,
        "supports_tools": True,
        "supports_thinking": False,
    },
    "qwen3-235b": {
        "model_id": "qwen3-235b",
        "context_limit": 128000,
        "cost_per_1k_tokens": 0.01,
        "supports_tools": True,
        "supports_thinking": True,
    },
}

# 2. 智能路由
class ModelRouter:
    def __init__(self):
        self._usage_stats: dict[str, int] = {}

    def route(self, context: RoutingContext) -> str:
        """根据上下文选择最合适的模型"""
        # 只读任务 → 廉价模型
        if context.task_type == "read_only":
            return "qwen3-8b"

        # 需要思维链 → reasoning 模型
        if context.requires_deep_reasoning:
            return "deepseek-reasoner"

        # 需要工具调用 → chat 模型
        if context.needs_tool_calling:
            if context.complexity == "high":
                return "qwen3-235b"
            else:
                return "deepseek-chat"

        # 默认
        return "deepseek-chat"

    def get_usage_stats(self) -> dict:
        """获取模型使用统计"""
        return self._usage_stats

# 3. 成本追踪
class CostTracker:
    def __init__(self):
        self._costs: dict[str, float] = {}

    def record_cost(self, model: str, input_tokens: int, output_tokens: int):
        """记录成本"""
        profile = MODEL_PROFILES[model]
        cost = (
            input_tokens * profile["cost_per_1k_tokens"] / 1000 +
            output_tokens * profile["cost_per_1k_tokens"] / 1000
        )
        self._costs[model] = self._costs.get(model, 0) + cost

    def get_total_cost(self) -> float:
        """获取总成本"""
        return sum(self._costs.values())

    def generate_cost_report(self) -> str:
        """生成成本报告"""
        lines = ["模型成本报告:"]
        for model, cost in sorted(self._costs.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {model}: ¥{cost:.4f}")
        lines.append(f"  总计: ¥{self.get_total_cost():.4f}")
        return "\n".join(lines)
```

**预期收益**：
- 💰 降低 40-60% 的 LLM 成本
- ⚡ 提升响应速度（廉价模型更快）
- 🎯 任务与模型匹配更优

---

## 二、OpsAgent 可以优化的地方

### 1️⃣ **任务编排能力增强**

#### 现状
```python
# OpsAgent: 简单的任务状态机
TaskStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]
```

#### 优化建议
```python
# 1. 任务依赖管理
class TaskDependencyManager:
    """任务依赖管理器"""

    def add_dependency(self, task_id: str, depends_on: str):
        """添加任务依赖"""
        self._dependencies[task_id].append(depends_on)

    def get_ready_tasks(self) -> List[str]:
        """获取可执行任务（依赖已完成）"""
        ready = []
        for task_id in self._pending_tasks:
            deps = self._dependencies.get(task_id, [])
            if all(self._is_completed(d) for d in deps):
                ready.append(task_id)
        return ready

    def resolve_dependency_graph(self, tasks: List[Task]) -> List[List[Task]]:
        """解析依赖图，返回执行层级"""
        # 返回 [[task1, task2], [task3], [task4]]
        # 第一层可并行，第二层等待第一层完成...
        pass

# 2. 并行执行
class ParallelTaskExecutor:
    """并行任务执行器"""

    async def execute_batch(self, tasks: List[Task]) -> List[TaskResult]:
        """并行执行一批任务"""
        async with asyncio.TaskGroup() as tg:
            results = [
                tg.create_task(self._execute_single_task(task))
                for task in tasks
            ]
        return [r.result() for r in results]

# 3. 工作流 DSL
class WorkflowDSL:
    """工作流 DSL"""

    @staticmethod
    def from_yaml(yaml_str: str) -> Workflow:
        """从 YAML 定义工作流"""
        pass

    @staticmethod
    def from_python(code: str) -> Workflow:
        """从 Python 代码定义工作流"""
        pass

# 示例
workflow_yaml = """
name: 磁盘清理
triggers:
  - type: cron
    schedule: "0 */6 * * *"
steps:
  - name: 检查磁盘
    type: check_disk
    params:
      threshold: 85
  - name: 识别目录
    type: identify_dirs
    depends_on: 检查磁盘
actions:
  - name: 快照
    type: snapshot
  - name: 清理
    type: cleanup
    depends_on: 快照
  - name: 通知
    type: notify
    parallel_with: 清理
"""
```

---

### 2️⃣ **错误处理增强**

#### 现状
```python
# OpsAgent: 简单的异常捕获
except Exception as e:
    self.logger.error(f"Error: {e}")
```

#### 优化建议
```python
# 1. 结构化错误类型
class OpsAgentError(Exception):
    """OpsAgent 基础错误"""
    error_code: str
    error_type: str
    recoverable: bool = False
    suggestion: str = ""

class PermissionDeniedError(OpsAgentError):
    """权限拒绝"""
    recoverable: False
    suggestion: "联系管理员申请权限"

class ToolExecutionError(OpsAgentError):
    """工具执行错误"""
    recoverable: True
    suggestion: "检查工具参数或重试"

class LLMError(OpsAgentError):
    """LLM 调用错误"""
    recoverable: True
    suggestion = "稍后重试或切换模型"

# 2. 错误恢复策略
class ErrorRecoveryStrategy:
    """错误恢复策略"""

    async def recover(self, error: OpsAgentError, context: ErrorContext) -> bool:
        """尝试恢复"""
        if isinstance(error, ToolExecutionError):
            return await self._recover_tool_error(error, context)
        elif isinstance(error, LLMError):
            return await self._recover_llm_error(error, context)
        return False

    async def _recover_tool_error(self, error: ToolExecutionError, context: ErrorContext):
        """恢复工具错误"""
        # 尝试重试
        if context.retry_count < 3:
            await asyncio.sleep(2 ** context.retry_count)  # 指数退避
            return True

        # 尝试降级策略
        fallback_result = await self._try_fallback(context)
        if fallback_result:
            return True

        return False

    async def _try_fallback(self, context: ErrorContext) -> bool:
        """尝试降级策略"""
        # 例如：工具 A 失败，尝试工具 B
        pass

# 3. 错误日志增强
class ErrorLogger:
    """错误日志增强"""

    def log_error(self, error: OpsAgentError, context: dict):
        """记录结构化错误日志"""
        log_entry = {
            "timestamp": time.time(),
            "error_code": error.error_code,
            "error_type": error.error_type,
            "message": str(error),
            "recoverable": error.recoverable,
            "suggestion": error.suggestion,
            "context": context,
            "traceback": traceback.format_exc(),
        }
        self._write_log(log_entry)

        # 触发告警
        if not error.recoverable:
            self._trigger_alert(error)
```

---

### 3️⃣ **缓存机制**

#### 现状
```python
# OpsAgent: 没有缓存
# 每次都重新计算
```

#### 优化建议
```python
# 1. 多级缓存
class MultiLevelCache:
    """多级缓存"""

    def __init__(self):
        self._l1_cache: dict[str, tuple] = {}  # 内存缓存
        self._l2_cache = DiskCache()           # 磁盘缓存

    async def get(self, key: str) -> Any | None:
        """获取缓存"""
        # L1 命中
        if key in self._l1_cache:
            value, expiry = self._l1_cache[key]
            if time.time() < expiry:
                return value
            del self._l1_cache[key]

        # L2 命中
        value = await self._l2_cache.get(key)
        if value:
            self._l1_cache[key] = (value, time.time() + 60)  # L1 缓存 60 秒
            return value

        return None

    async def set(self, key: str, value: Any, ttl: int = 3600):
        """设置缓存"""
        self._l1_cache[key] = (value, time.time() + 60)
        await self._l2_cache.set(key, value, ttl)

# 2. 感知结果缓存
class PerceptionCache:
    """感知结果缓存"""

    async def get_system_snapshot(self, force_refresh: bool = False):
        """获取系统快照（带缓存）"""
        if not force_refresh:
            cached = await self._cache.get("system_snapshot")
            if cached:
                return cached

        # 重新收集
        snapshot = await self._collect_snapshot()
        await self._cache.set("system_snapshot", snapshot, ttl=300)
        return snapshot

# 3. LLM 响应缓存
class LLMResponseCache:
    """LLM 响应缓存"""

    async def get_completion(self, messages: list, model: str):
        """获取缓存的 LLM 响应"""
        cache_key = self._generate_cache_key(messages, model)
        cached = await self._cache.get(cache_key)
        if cached:
            logger.info(f"LLM cache hit: {cache_key[:16]}...")
            return cached

        # 调用 LLM
        response = await self._call_llm(messages, model)
        await self._cache.set(cache_key, response, ttl=3600)
        return response

    def _generate_cache_key(self, messages: list, model: str) -> str:
        """生成缓存键"""
        content = json.dumps({"messages": messages, "model": model}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()
```

---

### 4️⃣ **可观测性增强**

#### 现状
```python
# OpsAgent: 基础日志
# 缺少 metrics 和 tracing
```

#### 优化建议
```python
# 1. Metrics 收集
class MetricsCollector:
    """指标收集器"""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list] = {}

    def increment(self, name: str, value: int = 1, tags: dict = None):
        """增加计数器"""
        key = self._make_key(name, tags)
        self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, name: str, value: float, tags: dict = None):
        """设置仪表盘"""
        key = self._make_key(name, tags)
        self._gauges[key] = value

    def record_histogram(self, name: str, value: float, tags: dict = None):
        """记录直方图"""
        key = self._make_key(name, tags)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def get_metrics(self) -> dict:
        """获取所有指标"""
        return {
            "counters": self._counters,
            "gauges": self._gauges,
            "histograms": self._get_histogram_stats(),
        }

    def _get_histogram_stats(self) -> dict:
        """计算直方图统计"""
        stats = {}
        for key, values in self._histograms.items():
            if values:
                stats[key] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "p50": np.percentile(values, 50),
                    "p95": np.percentile(values, 95),
                    "p99": np.percentile(values, 99),
                }
        return stats

# 2. Distributed Tracing
class Tracer:
    """分布式追踪"""

    @contextmanager
    def trace(self, operation: str, tags: dict = None):
        """追踪操作"""
        span_id = str(uuid.uuid4())[:8]
        logger.info(f"[{span_id}] Start: {operation}")
        start = time.time()

        try:
            yield {"span_id": span_id}
        finally:
            elapsed = time.time() - start
            logger.info(f"[{span_id}] End: {operation} ({elapsed:.3f}s)")
            self._record_operation(operation, elapsed, tags)

# 3. 健康检查
class HealthChecker:
    """健康检查"""

    async def check(self) -> HealthStatus:
        """执行健康检查"""
        status = HealthStatus(healthy=True)

        # 检查 LLM 连接
        try:
            await self._check_llm_connection()
            status.components["llm"] = "healthy"
        except Exception as e:
            status.components["llm"] = f"unhealthy: {e}"
            status.healthy = False

        # 检查数据库
        try:
            await self._check_database()
            status.components["database"] = "healthy"
        except Exception as e:
            status.components["database"] = f"unhealthy: {e}"
            status.healthy = False

        # 检查磁盘空间
        disk_usage = self._check_disk_space()
        if disk_usage > 90:
            status.components["disk"] = "warning: high usage"
        else:
            status.components["disk"] = "healthy"

        return status
```

---

### 5️⃣ **配置管理增强**

#### 现状
```python
# OpsAgent: 简单的环境变量配置
# 缺乏配置版本管理
```

#### 优化建议
```python
# 1. 配置中心
class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self._configs: dict[str, ConfigVersion] = {}
        self._active_version: str = None

    def load_config(self, config_path: str):
        """加载配置"""
        with open(config_path) as f:
            config = yaml.safe_load(f)

        version = str(uuid.uuid4())[:8]
        self._configs[version] = ConfigVersion(
            version=version,
            config=config,
            created_at=time.time(),
        )

        if not self._active_version:
            self._active_version = version

        return version

    def get_config(self, version: str = None) -> dict:
        """获取配置"""
        version = version or self._active_version
        return self._configs[version].config

    def rollback(self, version: str):
        """回滚到指定版本"""
        if version not in self._configs:
            raise ValueError(f"Version {version} not found")
        self._active_version = version

    def diff(self, v1: str, v2: str) -> list:
        """比较两个版本的差异"""
        c1 = self._configs[v1].config
        c2 = self._configs[v2].config
        return self._compare_dicts(c1, c2)

# 2. 配置热更新
class HotReload:
    """配置热更新"""

    def __init__(self, config_manager: ConfigManager):
        self._config_mgr = config_manager
        self._watchers: dict[str, Callable] = {}

    def watch(self, config_path: str, callback: Callable):
        """监听配置变化"""
        self._watchers[config_path] = callback
        # 使用 watchdog 监听文件变化
        # 文件变化时调用 callback

# 3. 配置验证
class ConfigValidator:
    """配置验证器"""

    def validate(self, config: dict) -> ValidationResult:
        """验证配置"""
        errors = []
        warnings = []

        # 验证必需字段
        required_fields = ["model_profile", "mode", "max_turns"]
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")

        # 验证值范围
        if config.get("max_turns", 0) > 100:
            warnings.append("max_turns > 100 may cause performance issues")

        # 验证模型配置
        model = config.get("model_profile")
        if model not in MODEL_PROFILES:
            errors.append(f"Unknown model: {model}")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
```

---

## 三、Keep 可以借鉴 OpsAgent 的地方

### 1️⃣ **安全管道设计**
- OpsAgent 的 10 环节安全管道非常完善
- Hook 外置设计，热加载
- 最小权限执行

### 2️⃣ **令牌系统**
- 批量授权机制
- 自动失效
- 任务绑定

### 3️⃣ **三级回滚**
- 快照 → 补偿 → 恢复
- 失败可追溯
- 24 小时可回滚

### 4️⃣ **熔断机制**
- 连续失败自动停止
- 指数退避重试
- 防止雪崩

### 5️⃣ **8-phase 审计日志**
- 完整的执行链路
- 结构化记录
- 事后可追溯

---

## 四、总结与优先级

### 高优先级（立即实施）
1. **插件化架构** - 提升扩展性
2. **去重机制** - 降低成本
3. **缓存机制** - 提升性能
4. **错误处理增强** - 提升可靠性

### 中优先级（短期实施）
1. **工作流编排** - 支持复杂场景
2. **Web UI** - 提升用户体验
3. **多模型支持** - 成本优化
4. **可观测性** - 运维友好

### 低优先级（长期规划）
1. **事件关联** - AI 增强
2. **多租户** - SaaS 支持
3. **摘要生成** - 自动化报告

---

**最终建议**：
- **OpsAgent + Keep** = 完美的 AIOps 平台
- OpsAgent 提供安全执行能力
- Keep 提供告警管理和工作流编排
- 两者可以深度融合，互为补充！
