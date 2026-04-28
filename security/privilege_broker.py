"""
security/privilege_broker.py — 最小权限执行层

对应 DOC-6 v1.1：所有系统命令在受限账号下执行，主进程永不直接操作系统资源。

账号体系：
  ai-runner (主进程)
    ├── ops-reader  (uid=9001) — 只读：df/ps/cat/ls/journalctl
    ├── ops-file    (uid=9002) — 文件操作：rm/mv/cp/tar
    └── ops-service (uid=9003) — 服务操作：systemctl/chmod/kill

Fail Closed：_preflight_check() 启动时验证，失败则 raise RuntimeError，拒绝启动。
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from config import AgentConfig

logger = logging.getLogger(__name__)

Privilege = Literal["reader", "file", "service"]

SAFE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "KUBECONFIG", "AWS_PROFILE"}

READER_USER = "ops-reader"
FILE_USER = "ops-file"
SERVICE_USER = "ops-service"

_DEFAULT_BASE_DIR = Path("/var/lib/opsagent")

_PRIVILEGE_TO_USER: dict[Privilege, str] = {
    "reader": READER_USER,
    "file": FILE_USER,
    "service": SERVICE_USER,
}


@dataclass
class ExecResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: float
    op_id: str
    privilege: Privilege
    script_path: str  # 已删除，仅用于审计记录


class PrivilegeBroker:
    """最小权限执行代理。

    通过 sudo 将命令路由到对应受限账号执行，主进程不直接操作系统资源。
    """

    def __init__(self, config: "AgentConfig") -> None:
        self._sudo_path = shutil.which("sudo") or "/usr/bin/sudo"
        # BASE_DIR 支持环境变量覆盖，便于容器化和多实例部署
        base_dir = Path(os.getenv("OPSAGENT_BASE_DIR", str(_DEFAULT_BASE_DIR)))
        self._script_dirs: dict[Privilege, Path] = {
            "reader": base_dir / "read_scripts",
            "file": base_dir / "file_scripts",
            "service": base_dir / "service_scripts",
        }
        self._preflight_check()

    def execute(
        self,
        cmd: str,
        op_id: str,
        privilege: Privilege,
        timeout: int | None = None,
    ) -> ExecResult:
        """以指定权限账号执行命令。

        privilege 由调用方（PermissionManager / ToolRegistry）静态决定，
        不接受来自 LLM 的 cmd_type 字符串，消除 LLM 控制执行账号的攻击面。

        默认无超时限制，由用户自行 Ctrl+C 中断。后台/auto 模式可通过
        timeout 参数显式设置上限。
        """
        user = _PRIVILEGE_TO_USER[privilege]
        script_path = self._write_script(cmd, privilege, op_id)
        safe_env = self._build_safe_env()
        t0 = time.monotonic()

        # 构建命令数组：无超时时跳过 timeout wrapper
        cmd_parts: list[str] = [self._sudo_path, "-u", user]
        if timeout is not None:
            cmd_parts.extend(["timeout", str(timeout)])
        cmd_parts.extend(["/bin/bash", str(script_path)])

        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=timeout + 5 if timeout is not None else None,
                env=safe_env,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            timed_out = (timeout is not None and result.returncode == 124)
            return ExecResult(
                success=(result.returncode == 0),
                stdout=result.stdout,
                stderr=f"执行超时（{timeout}s）" if timed_out else result.stderr,
                exit_code=result.returncode,
                elapsed_ms=elapsed_ms,
                op_id=op_id,
                privilege=privilege,
                script_path=str(script_path),
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ExecResult(
                success=False,
                stdout="",
                stderr=f"执行超时（{timeout}s，Python 兜底）",
                exit_code=-1,
                elapsed_ms=elapsed_ms,
                op_id=op_id,
                privilege=privilege,
                script_path=str(script_path),
            )
        finally:
            self._cleanup(script_path)

    @staticmethod
    def category_to_privilege(category: str) -> Privilege:
        """将 ToolRegistry 的 category 映射到 Privilege。

        category 来自工具注册时的静态声明或 CommandRiskResult（规则引擎输出），
        不来自 LLM。未知 category 保守降级到 reader。
        """
        mapping: dict[str, Privilege] = {
            "read":    "reader",
            "file":    "file",
            "service": "service",
        }
        privilege = mapping.get(category)
        if privilege is None:
            logger.warning("未知 category=%r，降级到 reader 账号", category)
            return "reader"
        return privilege

    def _preflight_check(self) -> None:
        """启动时验证安全环境，任何一项失败则 raise RuntimeError（Fail Closed）。"""
        errors: list[str] = []

        # 1. 三个账号存在
        for user in (READER_USER, FILE_USER, SERVICE_USER):
            try:
                result = subprocess.run(
                    ["getent", "passwd", user],
                    capture_output=True, timeout=5,
                )
                if result.returncode != 0:
                    errors.append(f"账号不存在: {user}")
            except Exception as e:
                errors.append(f"getent 检查失败 ({user}): {e}")

        # 2. 脚本目录存在且权限正确
        for privilege, script_dir in self._script_dirs.items():
            if not script_dir.exists():
                errors.append(f"脚本目录不存在: {script_dir}")
                continue
            st = script_dir.stat()
            if st.st_uid != os.getuid():
                errors.append(f"目录 owner 不匹配: {script_dir} (uid={st.st_uid}, 期望={os.getuid()})")
            mode = stat.S_IMODE(st.st_mode)
            if mode not in (0o750, 0o2750):
                errors.append(f"目录权限不合规: {script_dir} (需为 0o750 或 0o2750, 实际={oct(mode)})")
            elif mode == 0o750:
                # 提示但暂不中断，提醒开发者加上 SGID 以免后续创建脚本时报错
                logger.warning(f"[安全提示] 目录 {script_dir} 未开启 SGID(g+s)，建议执行: sudo chmod g+s {script_dir}")

        # 3. sudo -l 能列出 opsagent 规则
        try:
            result = subprocess.run(
                [self._sudo_path, "-l"],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout + result.stderr
            missing_rules = [u for u in ("ops-reader", "ops-file", "ops-service") if u not in output]
            if missing_rules:
                errors.append(f"sudoers 缺少规则: {missing_rules}")
        except Exception as e:
            errors.append(f"sudo -l 检查失败: {e}")

        # 4. visudo 语法验证
        try:
            result = subprocess.run(
                [self._sudo_path, "visudo", "-c"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                errors.append("visudo -c 语法检查失败")
        except FileNotFoundError:
            pass  # visudo 不存在时跳过（某些最小化环境）
        except Exception as e:
            errors.append(f"visudo 检查失败: {e}")

        # 5. 测试实际执行（echo ok via ops-reader）
        if not errors:
            try:
                test_result = subprocess.run(
                    [self._sudo_path, "-u", READER_USER, "/bin/echo", "ok"],
                    capture_output=True, text=True, timeout=10,
                )
                if test_result.returncode != 0 or "ok" not in test_result.stdout:
                    errors.append(f"sudo -u ops-reader 测试执行失败: {test_result.stderr}")
            except Exception as e:
                errors.append(f"sudo 测试执行异常: {e}")

        if errors:
            msg = "PrivilegeBroker 预检失败（Fail Closed）:\n" + "\n".join(f"  - {e}" for e in errors)
            raise RuntimeError(msg)

        # 6. 清理上次进程异常退出遗留的脚本（OOM/kill -9 时 finally 不执行）
        self._gc_stale_scripts()
        logger.info("PrivilegeBroker 预检通过，安全环境就绪")

    def _gc_stale_scripts(self) -> None:
        """清理脚本目录中的历史遗留文件（进程崩溃时 finally 未执行所致）。"""
        for script_dir in self._script_dirs.values():
            if not script_dir.exists():
                continue
            for stale in script_dir.glob("job_*.sh"):
                try:
                    stale.unlink()
                    logger.warning("清理遗留脚本: %s", stale)
                except Exception as e:
                    logger.warning("遗留脚本清理失败 %s: %s", stale, e)


    def _write_script(self, cmd: str, privilege: Privilege, op_id: str) -> Path:
            """用 mkstemp 创建临时脚本。"""
            try:

                script_dir = self._script_dirs[privilege]
               
                self._verify_script_dir(script_dir)

                # 获取目录的属组（关键：匹配opsagent-read/file/service组）
                dir_st = os.stat(script_dir)
                dir_gid = dir_st.st_gid

                # 创建临时文件
                fd, path_str = tempfile.mkstemp(
                    suffix=".sh",
                    dir=script_dir,
                    prefix=f"job_{op_id}_",
                )
                try:
                    content = f"#!/bin/bash\nset -uo pipefail\n{cmd}\n"
                    os.write(fd, content.encode("utf-8"))
                finally:
                    os.close(fd)

                #修复：检查文件属组，如果已经是目录的组（通过目录的SGID继承），则无需 chown
                file_st = os.stat(path_str)
                if file_st.st_gid != dir_gid:
                    try:
                        # -1 表示不改变属主，只尝试修改属组
                        os.chown(path_str, -1, dir_gid)

                    except PermissionError:
                        print(f"[WARNING] 无法将文件属组修改为 {dir_gid}，因为当前用户(UID:{os.getuid()})不在该组中。")
                        print(f"[WARNING] 请确保已在终端执行目录 SGID 设置: sudo chmod g+s {script_dir}")
    
                # 权限 640：属主可读写，组内可读，符合最小权限
                os.chmod(path_str, 0o640)
                
                return Path(path_str)

            except Exception as e:
                print(f"\n[FATAL] ❌ _write_script 彻底失败: {str(e)}")
                import traceback
                traceback.print_exc()
                raise RuntimeError(f"创建脚本失败: {e}") from e


    def _verify_script_dir(self, script_dir: Path) -> None:
        """验证脚本目录 owner 和权限，不符合则拒绝执行并告警。"""
        if not script_dir.exists():
            raise RuntimeError(f"脚本目录不存在: {script_dir}")
        st = script_dir.stat()
        if st.st_uid != os.getuid():
            raise RuntimeError(f"脚本目录 owner 被篡改: {script_dir}")
        mode = stat.S_IMODE(st.st_mode)
        if mode not in (0o750, 0o2750):
            raise RuntimeError(f"脚本目录权限被篡改: {script_dir} (需为0o750或0o2750，实际={oct(mode)})")

    def _build_safe_env(self) -> dict[str, str]:
        """从当前环境提取白名单变量，防止 LD_PRELOAD 等注入。
        强制覆盖 PATH 为标准系统路径，防止 sudo 重置后命令找不到。
        """
        safe_env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
        safe_env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        return safe_env

    def _cleanup(self, script_path: Path) -> None:
        """执行完立即删除脚本，不留痕迹。"""
        try:
            script_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("脚本清理失败 %s: %s", script_path, e)
        pass

