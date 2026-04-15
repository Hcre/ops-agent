"""
main.py — OpsAgent REPL 入口

启动顺序：
1. 加载 .env，校验 API Key
2. 检查 ops-reader(uid=9001) / ops-writer(uid=9002) 系统账号（Week 4 启用）
3. 初始化 AgentLoop
4. 进入主 REPL 循环
"""
from __future__ import annotations

import asyncio
import os
import sys


def _check_env() -> None:
    """校验必要的环境变量。"""
    from config import AgentConfig
    try:
        config = AgentConfig()
        config.get_api_key()
    except RuntimeError as e:
        print(f"[启动失败] {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"[配置错误] {e}")
        sys.exit(1)


def _check_privilege_users() -> None:
    """检查最小权限账号是否存在（Week 4 启用）。

    Week 1 stub: 跳过检查，打印提示。
    Week 4: 真实检查 ops-reader(9001) / ops-writer(9002)。
    """
    # TODO Week 4: 启用真实检查
    # import pwd
    # for name, uid in [("ops-reader", 9001), ("ops-writer", 9002)]:
    #     try:
    #         pw = pwd.getpwnam(name)
    #         if pw.pw_uid != uid:
    #             print(f"[警告] {name} uid={pw.pw_uid}，期望 {uid}")
    #     except KeyError:
    #         print(f"[启动失败] 系统账号 {name}(uid={uid}) 不存在")
    #         print(f"  创建命令: sudo useradd -u {uid} -r -s /sbin/nologin {name}")
    #         sys.exit(1)
    pass


async def main() -> None:
    _check_env()
    _check_privilege_users()

    from config import AgentConfig
    from core.agent_loop import AgentLoop

    config = AgentConfig()
    loop = AgentLoop(config)
    await loop.run()


if __name__ == "__main__":
    asyncio.run(main())
