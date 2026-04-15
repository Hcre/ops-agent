"""
security/prompt_injection.py — 提示词注入检测器

三层防御：
  Layer 1: 规则引擎（同步）— 明显的教科书级注入
  Layer 2: 启发式评分（同步）— 语义绕过、角色扮演、权限暗示
  Layer 3: LLM 审查（异步）— 只对 SUSPICIOUS 调用，与 IntentClassifier 共用模型

间接注入（工具输出中的注入）通过 detect_in_tool_output() 单独处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Layer 1: 规则引擎 — 明显的直接注入
# ---------------------------------------------------------------------------

# 直接注入：明确的角色覆盖 / 指令覆盖
_DIRECT_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"forget\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"you\s+are\s+now\s+a?\s*(new|different|evil|unrestricted|jailbreak)", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+are\s+)?a?\s*(jailbreak|dan|evil|unrestricted)", re.I),
    re.compile(r"\[system\]\s*:?\s*(ignore|override|bypass|you are)", re.I),
    re.compile(r"<system>\s*(ignore|override|bypass)", re.I),
    # 中文直接注入
    re.compile(r"忽略(之前|前面|所有)(的)?(指令|规则|限制|提示)"),
    re.compile(r"你现在是.{0,20}(不受限|无限制|邪恶|黑客|越狱)"),
    re.compile(r"(覆盖|重置|清除).{0,10}(系统提示|system prompt|指令)"),
]

# 间接注入：工具输出中常见的注入载体
# 攻击者在日志/文件/命令输出里埋入指令
_INDIRECT_PATTERNS: list[re.Pattern] = [
    # 伪造系统消息格式
    re.compile(r"\[SYSTEM\]\s*[A-Z]", re.M),           # [SYSTEM] Execute: ...
    re.compile(r"<\|system\|>", re.I),                  # <|system|> token
    re.compile(r"<<SYS>>", re.I),                       # Llama 格式
    re.compile(r"###\s*System:", re.I),                 # Markdown 伪系统消息
    # 伪造 assistant 消息
    re.compile(r"\[ASSISTANT\]\s*:", re.I),
    re.compile(r"<\|assistant\|>", re.I),
    # 嵌入式指令（日志/文件里常见）
    re.compile(r"EXECUTE\s*:\s*(rm|chmod|curl|wget|bash|sh)\s", re.I),
    re.compile(r"RUN\s*:\s*(rm|chmod|curl|wget|bash|sh)\s", re.I),
    re.compile(r"CMD\s*:\s*(rm|chmod|curl|wget|bash|sh)\s", re.I),
]

# ---------------------------------------------------------------------------
# Layer 2: 启发式评分 — 语义绕过（无法用精确正则匹配）
# ---------------------------------------------------------------------------

# 每个特征命中加分，超过阈值 → SUSPICIOUS
_HEURISTIC_SIGNALS: list[tuple[re.Pattern, float]] = [
    # 角色扮演请求（绕过身份）
    (re.compile(r"(假设|假装|扮演|模拟).{0,20}(没有限制|不受约束|自由)", re.I), 2.0),
    (re.compile(r"(pretend|roleplay|act as).{0,30}(no restriction|unlimited|free)", re.I), 2.0),
    # 权限绕过暗示
    (re.compile(r"(跳过|绕过|忽略).{0,15}(安全|权限|检查|确认)", re.I), 2.5),
    (re.compile(r"(bypass|skip|ignore).{0,15}(security|permission|check|confirm)", re.I), 2.5),
    # 测试/调试模式声称
    (re.compile(r"(测试模式|debug mode|maintenance mode|开发模式)", re.I), 1.5),
    (re.compile(r"(this is a test|仅供测试|for testing only)", re.I), 1.0),
    # 权威声称（冒充管理员/系统）
    (re.compile(r"(我是|i am|as).{0,10}(管理员|root|admin|system|superuser)", re.I), 1.5),
    (re.compile(r"(authorized|已授权|有权限).{0,20}(执行|run|execute)", re.I), 1.5),
    # 泄露系统提示
    (re.compile(r"(print|show|reveal|output|输出|显示|打印).{0,20}(system prompt|系统提示|你的指令)", re.I), 2.0),
    # 中文语义绕过
    (re.compile(r"作为.{0,10}(没有限制|不受约束|自由发挥)"), 2.0),
    (re.compile(r"(不需要|无需).{0,10}(确认|审批|检查)"), 1.5),
]

_HEURISTIC_THRESHOLD = 3.0   # 超过此分数 → SUSPICIOUS


# ---------------------------------------------------------------------------
# 检测结果
# ---------------------------------------------------------------------------

InjectionVerdict = Literal["CLEAN", "SUSPICIOUS", "INJECTED"]


@dataclass
class InjectionResult:
    verdict: InjectionVerdict
    reason: str = ""
    score: float = 0.0          # Layer 2 启发式得分（SUSPICIOUS 时有意义）
    layer: int = 0              # 哪一层检测到（1/2/3）


# ---------------------------------------------------------------------------
# PromptInjectionDetector
# ---------------------------------------------------------------------------

class PromptInjectionDetector:
    """三层提示词注入检测器。

    用法：
        result = detector.check(text)
        if result.verdict == "INJECTED":
            # 硬阻断
        elif result.verdict == "SUSPICIOUS":
            # 可选：送 LLM 审查（Layer 3），或直接阻断

    detect() 是向后兼容的简化接口，返回 bool。
    """

    def check(self, text: str) -> InjectionResult:
        """完整三层检测，返回结构化结果。"""
        # Layer 1: 规则引擎
        for pattern in _DIRECT_PATTERNS:
            if pattern.search(text):
                return InjectionResult(
                    verdict="INJECTED",
                    reason=f"直接注入: 匹配规则 {pattern.pattern[:40]}",
                    layer=1,
                )

        # Layer 2: 启发式评分
        score = 0.0
        triggered = []
        for pattern, weight in _HEURISTIC_SIGNALS:
            if pattern.search(text):
                score += weight
                triggered.append(pattern.pattern[:30])

        if score >= _HEURISTIC_THRESHOLD:
            return InjectionResult(
                verdict="SUSPICIOUS",
                reason=f"启发式评分 {score:.1f} >= {_HEURISTIC_THRESHOLD}，触发: {triggered[:2]}",
                score=score,
                layer=2,
            )

        return InjectionResult(verdict="CLEAN")

    def check_tool_output(self, output: str) -> InjectionResult:
        """检测工具输出中的间接注入。

        运维场景最危险的攻击面：攻击者在日志/文件/命令输出里埋入指令。
        只检测 Layer 1 间接模式（不做启发式，避免误报）。
        """
        for pattern in _INDIRECT_PATTERNS:
            if pattern.search(output):
                return InjectionResult(
                    verdict="INJECTED",
                    reason=f"间接注入（工具输出）: 匹配 {pattern.pattern[:40]}",
                    layer=1,
                )
        return InjectionResult(verdict="CLEAN")

    # ------------------------------------------------------------------
    # 向后兼容接口
    # ------------------------------------------------------------------

    def detect(self, text: str) -> bool:
        """简化接口：True = 检测到注入（INJECTED 或 SUSPICIOUS）。"""
        return self.check(text).verdict != "CLEAN"

    def detect_in_tool_output(self, output: str) -> bool:
        """简化接口：True = 工具输出中检测到间接注入。"""
        return self.check_tool_output(output).verdict != "CLEAN"
