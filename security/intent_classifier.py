"""
security/intent_classifier.py — 意图分类器（三层纵深防御）

职责：
  - 结构化命令风险分析，输出 CommandRiskResult
  - L1: 绝对黑名单（微秒级，硬编码）
  - L2: YAML 规则引擎（毫秒级，正则匹配）
  - L3: LLM 语义审查（秒级，灰色地带升级）

不做的事：
  - 不做 allow/ask/deny 决策（由 PermissionManager 负责）
  - 不判断"闲聊"（闲聊白名单以有穷对抗无穷）
"""
from __future__ import annotations

import json
import logging
import os
import pwd
import re
import shlex
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import yaml
from openai import AsyncOpenAI

from config import ABSOLUTE_BLACKLIST, HIGH_RISK_PREFIXES, READ_PREFIXES

if TYPE_CHECKING:
    from config import AgentConfig

RiskLevel = Literal["MEDIUM", "HIGH", "CRITICAL"]
CmdCategory = Literal["read", "file", "service", "unknown"]

# ---------------------------------------------------------------------------
# 复合命令 / 危险模式正则（从 permission_manager 移入，统一分类入口）
# ---------------------------------------------------------------------------

# 命令替换：$() 或反引号（shell 展开阶段执行，无法通过简单前缀匹配防御）
_CMD_SUBSTITUTION = re.compile(r'\$\(|`')

# 写重定向：> 或 >> 后跟非空路径（排除 heredoc << 和 here-string <<<）
_WRITE_REDIRECT = re.compile(r'(?<![<>])>>?(?![>])\s*\S')
# 无害重定向：/dev/null 和 fd 复制（2>&1），检测前先剔除
_SAFE_REDIRECT = re.compile(r'[12]?>>?\s*/dev/null|[12]?>>?\s*&[12]|&>\s*/dev/null')

# 管道右侧危险命令
_PIPE_DANGEROUS_RHS = re.compile(
    r'\|\s*(?:curl|wget|bash|sh|python|python3|perl|ruby|nc|ncat|socat)\b'
)

# 复合命令中的网络命令
_NETWORK_CMDS = re.compile(r'^(?:curl|wget|nc|ncat|socat)\b')


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CommandRiskResult:
    """tool_call 层的结构化审查结果，由 classify_command() 输出。"""
    risk_level:       Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason:           str
    blast_radius:     str
    reversible:       bool
    needs_human:      bool
    suggested_action: str
    classifier:       Literal["rule", "llm", "default"]
    category:         CmdCategory = "read"
    context:          str = "default"

    @property
    def target_user(self) -> str:
        if self.context == "default":
            return f"ops-{self.category}"
        return f"ops-{self.category}-{self.context}"


@dataclass
class IntentResult:
    """向后兼容：旧版 classify() 返回值，仅用于用户输入层的早期预警。"""
    risk_level: RiskLevel
    intent: str
    reason: str
    matched_pattern: str = ""
    classifier: Literal["rule"] = "rule"


@dataclass
class _CompiledRule:
    id: str
    description: str
    intent: str
    risk_level: RiskLevel
    patterns: list[re.Pattern] = field(default_factory=list)


@dataclass
class _L3AuditContext:
    """收集系统硬事实，注入 L3 prompt。不由主 LLM 输出决定。"""
    cmd: str
    cwd: str
    uid: int
    username: str
    l2_signal: str
    available_accounts: list[dict]  # {name, uid, description}


# ---------------------------------------------------------------------------
# 命令规范化与拆分工具
# ---------------------------------------------------------------------------

def normalize_cmd(cmd: str) -> str:
    """使用 shlex 规范化命令，消除引号绕过手法。

    shlex.split() 正确处理 shell 引号规则：
      r'm' -rf /   →  ['rm', '-rf', '/']  →  'rm -rf /'
      /et''c/      →  ['/etc/']           →  '/etc/'
      echo "hello" →  ['echo', 'hello']   → 不丢失内容
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # 引号不匹配，可能恶意也可能手误，返回原始输入让正则引擎处理
        return re.sub(r"\s+", " ", cmd).strip()
    return " ".join(tokens)


def has_command_substitution(cmd: str) -> bool:
    """检测是否包含 $() 或反引号命令替换。

    命令替换在 shell 展开阶段执行，ls $(rm -rf /) 会被简单前缀匹配
    误判为只读 ls。必须检测并升级处理。
    """
    return bool(_CMD_SUBSTITUTION.search(cmd))


def has_redirection(cmd: str) -> bool:
    """检测是否包含写重定向（> 或 >>），排除 /dev/null 和 fd 复制等无害模式。"""
    cleaned = _SAFE_REDIRECT.sub('', cmd)
    return bool(_WRITE_REDIRECT.search(cleaned))


def has_dangerous_pipe(cmd: str) -> bool:
    """检测管道右侧是否为危险命令（curl/wget 等数据外泄工具）。"""
    return bool(_PIPE_DANGEROUS_RHS.search(cmd))


_SEPARATOR_RE = re.compile(r'&&|\|\||[;|]')

def _normalize_separators(cmd: str) -> str:
    """在 shell 分隔符前后插入空格，使 shlex 能正确拆分。

    ls; rm -rf /  →  ls ; rm -rf /
    ls&&rm        →  ls && rm
    """
    return _SEPARATOR_RE.sub(r' \g<0> ', cmd)

def split_commands(cmd: str) -> list[str]:
    """使用 shlex 将复合命令拆分为独立子命令。

    拆分分隔符：; && || |
    正确处理引号：echo "a && b" 不会在 && 处拆分。

    返回子命令列表。
    """
    # 先在分隔符前后插入空格，确保 shlex 能正确识别
    normalized = _normalize_separators(cmd)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        # 引号不匹配时回退到简单按分隔符拆分
        return _fallback_split(cmd)

    parts: list[str] = []
    current: list[str] = []
    separators = {";", "&&", "||", "|"}

    for token in tokens:
        if token in separators:
            if current:
                parts.append(" ".join(current))
                current = []
        else:
            current.append(token)
    if current:
        parts.append(" ".join(current))

    return parts if parts else [cmd]


def _fallback_split(cmd: str) -> list[str]:
    """引号不匹配时的回退拆分（按 ; && || | 分隔）。"""
    # 用简单正则拆分，接受边界情况
    parts = re.split(r'\s*[;&|]+\s*', cmd)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# L3 审计上下文收集与 Prompt 构建（独立函数，不依赖 IntentClassifier 实例）
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_ACCOUNT_DEFINITIONS = [
    {"name": "ops-reader", "uid": 9001, "description": "read-only — df, ps, cat, ls, journalctl, grep, stat"},
    {"name": "ops-file", "uid": 9002, "description": "file operations — rm, mv, cp, tar, mkdir, chown, chmod"},
    {"name": "ops-service", "uid": 9003, "description": "service operations — systemctl, kill, service, reboot"},
]


def _collect_audit_context(cmd: str, l2_signal: str) -> _L3AuditContext:
    """收集系统硬事实，绝不依赖主 LLM 输出。"""
    return _L3AuditContext(
        cmd=cmd,
        cwd=os.getcwd(),
        uid=os.getuid(),
        username=pwd.getpwuid(os.getuid()).pw_name,
        l2_signal=l2_signal,
        available_accounts=_ACCOUNT_DEFINITIONS,
    )


def _xml_escape(text: str) -> str:
    """转义 XML 特殊字符，防止标签注入。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_l3_prompt(ctx: _L3AuditContext) -> str:
    """构建沙箱化 L3 审查 prompt。XML 标签隔离命令文本，防止 prompt injection。

    命令文本经过 XML 转义，防止攻击者通过 </command_to_review>
    等手法提前关闭标签并注入伪造的系统上下文。
    """
    accounts_text = "\n".join(
        f"  - {a['name']} (uid={a['uid']}): {a['description']}"
        for a in ctx.available_accounts
    )
    return f"""<system_context>
Working Directory: {ctx.cwd}
User: {ctx.username} (uid={ctx.uid})

Available System Accounts:
{accounts_text}
</system_context>

<l2_signal>
{_xml_escape(ctx.l2_signal)}
</l2_signal>

<command_to_review>
{_xml_escape(ctx.cmd)}
</command_to_review>

Analyze the command above as a senior Linux security auditor. Determine its actual effects, risk level, and which system account it should route to. Output JSON only, no other text:
{{"risk_level":"LOW|MEDIUM|HIGH|CRITICAL","reason":"用中文简短说明风险原因","blast_radius":"none|filesystem|services|network|full_system|unknown","reversible":true|false,"suggested_action":"allow|confirm|deny","category":"read|file|service|unknown"}}"""


def _parse_l3_response(raw: str) -> CommandRiskResult:
    """解析 L3 LLM 返回的 JSON，失败时安全降级为 CRITICAL。"""
    try:
        # 提取第一个 JSON 对象（防御 LLM 在 JSON 前后加额外文字）
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("L3 审查员返回不可解析的 JSON，降级为 CRITICAL")
        return CommandRiskResult(
            risk_level="CRITICAL",
            reason="L3 审查员返回格式异常，保守拒绝",
            blast_radius="unknown",
            reversible=False,
            needs_human=True,
            suggested_action="立即拒绝，审查解析失败",
            classifier="llm",
            category="unknown",
        )

    valid_risks = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    risk = data.get("risk_level", "CRITICAL")
    if risk not in valid_risks:
        risk = "CRITICAL"

    valid_categories = {"read", "file", "service", "unknown"}
    category = data.get("category", "unknown")
    if category not in valid_categories:
        category = "unknown"

    return CommandRiskResult(
        risk_level=risk,
        reason=data.get("reason", "L3 审查完成"),
        blast_radius=data.get("blast_radius", "未知"),
        reversible=bool(data.get("reversible", True)),
        needs_human=risk != "LOW",
        suggested_action=data.get("suggested_action", "建议人工确认"),
        classifier="llm",
        category=category,
    )


# ---------------------------------------------------------------------------
# IntentClassifier
# ---------------------------------------------------------------------------

class IntentClassifier:
    """三层纵深防御命令分类器。

    L1 — 绝对黑名单（微秒级）：硬编码，不可绕过
    L2 — YAML 规则引擎（毫秒级）：正则匹配，热加载
    L3 — LLM 语义审查（秒级）：MEDIUM 灰色地带升级审查

    复合命令短路规则：包含 ; && || | 的命令不经过快速白名单路径，
    强制走 L1+L2 高风险扫描。包含 $() 或反引号的命令直接升级。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._config = config
        self._rules: list[_CompiledRule] = []
        self._load_rules()

    # ------------------------------------------------------------------
    # L1: 绝对黑名单
    # ------------------------------------------------------------------

    def _check_l1_blacklist(self, cmd: str) -> CommandRiskResult | None:
        """L1 绝对黑名单检查。命中即 CRITICAL。

        将命令拆分为子命令后，对每个子命令做 startswith 匹配。
        避免 echo "don't use rm -rf /" 误伤（rm 在引号内不作为命令执行）。
        """
        # 拆分后对每个子命令独立检查（ls; rm -rf / 的第二子命令会被锚定）
        sub_commands = split_commands(cmd)
        for sub in sub_commands:
            sub_norm = normalize_cmd(sub).lower()
            for pattern in ABSOLUTE_BLACKLIST:
                if sub_norm.startswith(pattern):
                    return CommandRiskResult(
                        risk_level="CRITICAL",
                        reason=f"绝对黑名单命中: {pattern!r}",
                        blast_radius="全系统",
                        reversible=False,
                        needs_human=False,
                        suggested_action="立即拒绝，不可绕过",
                        classifier="rule",
                        category="unknown",
                    )
            # dd if= 兜底（覆盖不在精确黑名单中的 dd 写入模式）
            if sub_norm.startswith("dd if="):
                return CommandRiskResult(
                    risk_level="CRITICAL",
                    reason="危险前缀阻断: 'dd if='",
                    blast_radius="全系统",
                    reversible=False,
                    needs_human=False,
                    suggested_action="立即拒绝，不可绕过",
                    classifier="rule",
                    category="unknown",
                )
        return None

    # ------------------------------------------------------------------
    # L2: YAML 规则引擎
    # ------------------------------------------------------------------

    def _check_l2_rules(self, cmd: str) -> IntentResult | None:
        """L2 规则扫描，CRITICAL → HIGH → MEDIUM 优先级。"""
        for rule in self._rules:
            for pattern in rule.patterns:
                if pattern.search(cmd):
                    return IntentResult(
                        risk_level=rule.risk_level,
                        intent=rule.intent,
                        reason=f"规则 {rule.id}: {rule.description}",
                        matched_pattern=pattern.pattern,
                    )
        return None

    def _scan_compound(self, cmd: str) -> IntentResult | None:
        """对复合命令的每个子命令独立扫描，返回最高风险。"""
        parts = split_commands(cmd)
        risk_order = {"MEDIUM": 0, "HIGH": 1, "CRITICAL": 2}
        highest: IntentResult | None = None

        for part in parts:
            result = self._check_l2_rules(part)
            if result is None:
                continue
            # 复合命令中的网络命令升级为 HIGH
            if result.risk_level == "MEDIUM" and _NETWORK_CMDS.match(part.strip()):
                result = IntentResult(
                    risk_level="HIGH",
                    intent="network_data_exfil",
                    reason=f"复合命令中的网络命令（可能外泄数据）: {part.strip()!r}",
                    matched_pattern="compound_network",
                )
            if highest is None or risk_order.get(result.risk_level, 0) > risk_order.get(highest.risk_level, 0):
                highest = result

        return highest

    # ------------------------------------------------------------------
    # L3: LLM 语义审查
    # ------------------------------------------------------------------

    async def _check_l3_llm(self, cmd: str, context: str = "") -> CommandRiskResult:
        """L3 LLM 语义审查：收集系统硬事实 → 调用审查员 LLM → 解析结构化结果。

        API 不可用时安全降级为 MEDIUM + needs_human（保守兜底）。
        """
        ctx = _collect_audit_context(cmd, context)
        try:
            return await self._call_l3_llm(ctx)
        except Exception as exc:
            logger.warning("L3 审查员调用失败，降级为 MEDIUM: %s", exc)
            return CommandRiskResult(
                risk_level="MEDIUM",
                reason=f"L3 审查不可用（{exc}），保守按 MEDIUM 处理",
                blast_radius="未知",
                reversible=True,
                needs_human=True,
                suggested_action="建议人工确认后再执行",
                classifier="default",
                category="unknown",
            )

    async def _call_l3_llm(self, ctx: _L3AuditContext) -> CommandRiskResult:
        """构建 prompt + 调用审查员 LLM + 解析响应。"""
        profile = self._config.get_security_reviewer_profile()
        prompt = _build_l3_prompt(ctx)
        client = AsyncOpenAI(
            api_key=os.environ[profile["api_key_env"]],
            base_url=profile["base_url"],
            timeout=5.0,
        )
        response = await client.chat.completions.create(
            model=profile["model_id"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content or ""
        logger.debug("L3 审查员原始响应: %s", raw)
        return _parse_l3_response(raw)

    # ------------------------------------------------------------------
    # 综合分类（公开 API）
    # ------------------------------------------------------------------

    async def classify_command(self, cmd: str, mode: str = "default") -> CommandRiskResult:
        """对工具调用命令做全面的结构化风险分析。

        管道：
        1. 检测命令替换 → 直接 CRITICAL
        2. L1 黑名单 → CRITICAL 命中则直接返回
        3. 复合命令拆分 + 逐子命令扫描（短路：不走快速白名单）
        4. L2 规则扫描（原始 + 规范化双通道）
        5. 前缀匹配（只读白名单 / 高危黑名单）
        6. MEDIUM 灰色地带 → L3 LLM 审查
        7. 无匹配 → LOW 放行

        Returns:
            CommandRiskResult — 结构化风险分析结果
        """
        cmd_stripped = cmd.strip()

        # 0. 命令替换检测：$() 和反引号在执行时由 shell 展开，
        #    前缀匹配无法防御，直接升级为 CRITICAL
        if has_command_substitution(cmd_stripped):
            return CommandRiskResult(
                risk_level="CRITICAL",
                reason="命令包含 $() 或反引号命令替换，可能隐藏恶意操作，无法安全审查",
                blast_radius="未知",
                reversible=False,
                needs_human=False,
                suggested_action="立即拒绝，命令替换不可审查",
                classifier="rule",
                category="unknown",
            )

        # 1. L1 绝对黑名单（微秒级）
        l1_result = self._check_l1_blacklist(cmd_stripped)
        if l1_result:
            return l1_result

        # 2. 判断是否为复合命令（; && || |）
        #    使用 split_commands（shlex token 检测），正确处理 ls;rm 等无空格绕过
        sub_commands = split_commands(cmd_stripped)
        is_compound = len(sub_commands) > 1

        # 3. 写重定向检测 → L2 信号，路由到 L3 做语义审查
        if has_redirection(cmd_stripped):
            return await self._check_l3_llm(cmd_stripped, "检测到写重定向")

        # 4. 危险管道检测 → L2 信号，路由到 L3 做语义审查
        if has_dangerous_pipe(cmd_stripped):
            return await self._check_l3_llm(cmd_stripped, "检测到危险管道")

        # 5. L2 YAML 规则扫描
        #    复合命令：每个子命令独立扫描
        #    普通命令：原始 + 规范化双通道扫描
        l2_result: IntentResult | None = None
        if is_compound:
            l2_result = self._scan_compound(cmd_stripped)
        else:
            normalized = normalize_cmd(cmd_stripped)
            l2_raw = self._check_l2_rules(cmd_stripped)
            l2_norm = self._check_l2_rules(normalized) if normalized != cmd_stripped else None
            if l2_raw and l2_norm:
                order = {"MEDIUM": 0, "HIGH": 1, "CRITICAL": 2}
                l2_result = l2_raw if order[l2_raw.risk_level] <= order[l2_norm.risk_level] else l2_norm
            else:
                l2_result = l2_raw or l2_norm

        if l2_result:
            # TODO: YAML CRITICAL 长远也应走 L3（正则 ≠ 100% 确定），
            #       当前保留硬拦截是过渡期策略
            if l2_result.risk_level == "CRITICAL":
                category = _infer_category(l2_result.intent)
                return CommandRiskResult(
                    risk_level="CRITICAL",
                    reason=l2_result.reason,
                    blast_radius=_blast_radius(l2_result.intent),
                    reversible=False,
                    needs_human=False,
                    suggested_action="立即拒绝",
                    classifier="rule",
                    category=category,
                )
            # HIGH / MEDIUM / MEDIUM+复合 → 统一路由到 L3
            # L2 只做信号检测，不确定 severity/category 的语义判断
            return await self._check_l3_llm(cmd_stripped, l2_result.reason)

        # 6. 前缀匹配（只读白名单 / 高危黑名单）
        #    复合命令不经过快速白名单路径
        cmd_normalized = normalize_cmd(cmd_stripped)
        if not is_compound:
            for prefix in READ_PREFIXES:
                if cmd_normalized == prefix or cmd_normalized.startswith(prefix + " "):
                    return CommandRiskResult(
                        risk_level="LOW",
                        reason=f"只读命令（前缀匹配: {prefix}）",
                        blast_radius="无",
                        reversible=True,
                        needs_human=False,
                        suggested_action="自动放行",
                        classifier="rule",
                        category="read",
                    )
        for prefix in HIGH_RISK_PREFIXES:
            if cmd_normalized == prefix or cmd_normalized.startswith(prefix + " "):
                return CommandRiskResult(
                    risk_level="HIGH",
                    reason=f"高危命令（前缀匹配: {prefix}）",
                    blast_radius="文件系统/服务",
                    reversible=False,
                    needs_human=True,
                    suggested_action="需要用户确认",
                    classifier="rule",
                    category="file",
                )

        # 7. 复合命令：先检查是否所有子命令都是只读操作
        if is_compound:
            all_readonly = True
            for sub in sub_commands:
                sub_norm = normalize_cmd(sub)
                if not any(
                    sub_norm == p or sub_norm.startswith(p + " ")
                    for p in READ_PREFIXES
                ):
                    all_readonly = False
                    break
            if all_readonly:
                return CommandRiskResult(
                    risk_level="LOW",
                    reason="只读管道/复合命令（所有子命令均为只读操作）",
                    blast_radius="无",
                    reversible=True,
                    needs_human=False,
                    suggested_action="自动放行",
                    classifier="rule",
                    category="read",
                )
            return await self._check_l3_llm(cmd_stripped)

        # 8. 无任何匹配 → L3 审查（「不认识」≠「安全」）
        return await self._check_l3_llm(cmd_stripped, "未命中任何规则，需语义审查")

    # ------------------------------------------------------------------
    # 向后兼容：旧版 classify() 接口
    # ------------------------------------------------------------------

    async def classify(self, user_input: str) -> IntentResult | None:
        """旧版接口：扫描输入中的高危操作信号。

        返回 None → 未检测到高危信号
        返回 IntentResult → 检测到 MEDIUM/HIGH/CRITICAL
        """
        normalized = normalize_cmd(user_input)
        result_raw = self._check_l2_rules(user_input)
        result_norm = self._check_l2_rules(normalized) if normalized != user_input else None

        if result_raw and result_norm:
            order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
            return result_raw if order[result_raw.risk_level] <= order[result_norm.risk_level] else result_norm
        return result_raw or result_norm

    # ------------------------------------------------------------------
    # 规则加载
    # ------------------------------------------------------------------

    def _load_rules(self) -> None:
        rules_path = os.path.join(
            os.path.dirname(__file__), "rules", "intent_rules.yaml"
        )
        if not os.path.exists(rules_path):
            return
        try:
            with open(rules_path) as f:
                data = yaml.safe_load(f)

            raw_rules = [
                r for r in data.get("rules", [])
                if r.get("risk_level") in ("MEDIUM", "HIGH", "CRITICAL")
            ]
            order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
            raw_rules.sort(key=lambda r: order.get(r["risk_level"], 9))

            compiled = []
            for rule in raw_rules:
                patterns = []
                for p in rule.get("patterns", []):
                    try:
                        patterns.append(re.compile(p, re.IGNORECASE))
                    except re.error as e:
                        from core import ui
                        ui.print_error(f"[IntentClassifier] 规则 {rule['id']} pattern 编译失败: {p!r} — {e}")
                compiled.append(_CompiledRule(
                    id=rule["id"],
                    description=rule["description"],
                    intent=rule["intent"],
                    risk_level=rule["risk_level"],
                    patterns=patterns,
                ))
            self._rules = compiled

        except Exception as e:
            from core import ui
            ui.print_error(f"[IntentClassifier] 加载规则失败: {e}")

    def reload_rules(self) -> None:
        self._rules = []
        self._load_rules()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _infer_category(intent: str) -> CmdCategory:
    """从意图推断命令类别，用于路由到对应的 ops-* 账号。"""
    if intent in ("destructive_system_op", "kernel_level_manipulation", "privilege_escalation"):
        return "unknown"
    if intent in ("file_deletion", "config_modification", "software_removal"):
        return "file"
    if intent in ("service_control", "service_restart", "network_config_change"):
        return "service"
    if intent == "user_management":
        return "file"
    return "read"


def _blast_radius(intent: str) -> str:
    """估算爆炸半径。"""
    mapping = {
        "destructive_system_op": "全系统",
        "privilege_escalation": "全系统",
        "kernel_level_manipulation": "全系统",
        "file_deletion": "文件系统",
        "service_control": "服务/系统",
        "service_restart": "服务/系统",
        "network_config_change": "网络",
        "config_modification": "文件系统",
        "software_removal": "文件系统",
        "user_management": "账号系统",
    }
    return mapping.get(intent, "未知")
