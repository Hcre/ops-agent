"""
tests/test_deep_perception.py - 深度感知工具集成测试
目标：模拟主循环，展示工具返回给 LLM 的原始字符串。
"""
import asyncio
import os
import sys
import json
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich import box

# 确保可以导入项目根目录下的 tools 和 core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.perception_tools import (
    get_disk_detail, 
    get_process_detail, 
    get_logs, 
    get_network_detail, 
    get_system_snapshot
)
from core.agent_loop import ToolResult

console = Console()

def show_llm_view(res: ToolResult, title: str):
    """
    核心展示函数：模拟 LLM 接收到的消息格式
    """
    color = "green" if res.success else "red"
    
    # 打印元数据
    meta_table = Table.grid(padding=(0, 2))
    meta_table.add_row("[dim]Status:[/dim]", f"[bold {color}]{'SUCCESS' if res.success else 'FAILED'}[/bold {color}]")
    meta_table.add_row("[dim]Tool ID:[/dim]", f"[cyan]{res.tool_call_id}[/cyan]")
    meta_table.add_row("[dim]Latency:[/dim]", f"[yellow]{res.elapsed_ms:.2f}ms[/yellow]")
    
    # 打印输出内容（LLM 看到的正文）
    content = res.output if res.success else f"ERROR: {res.error}"
    
    # 根据内容类型选择高亮
    lexer = "markdown" if "##" in content else "text"
    if content.strip().startswith("{"): lexer = "json"

    console.print(Panel(
        meta_table, 
        title=f"[bold white]{title}[/bold white]", 
        border_style="dim", 
        expand=False
    ))
    
    console.print(Panel(
        Syntax(content, lexer, theme="monokai", word_wrap=True),
        title="[bold blue]LLM RECEIVE (role: tool)[/bold blue]",
        border_style="blue",
        padding=(1, 2)
    ))
    console.print("\n" + "="*80 + "\n")

async def run_tests():
    console.print(Panel.fit(
        " [bold yellow]OpsAgent 深度感知工具箱 - 内容审计测试[/bold yellow] ",
        box=box.DOUBLE_EDGE,
        subtitle="Testing logic & output formatting"
    ))

    # 1. 测试磁盘详情 (自动寻址逻辑)
    with console.status("[bold green]Test 1: Disk detail (Path: /var/log)..."):
        # 即使传的是目录，工具也应定位到挂载点
        res = await get_disk_detail("/var/log")
        show_llm_view(res, "Disk Detail Tool")

    # 2. 测试进程详情 (模式 A: 按名称搜索)
    with console.status("[bold green]Test 2: Process by Name (Search: python)..."):
        # 模拟 LLM 第一次尝试通过名称寻找
        res = await get_process_detail(name="python")
        show_llm_view(res, "Process Detail (Search Mode)")

    # 3. 测试进程详情 (模式 B: 按精确 PID 详情)
    my_pid = os.getpid()
    with console.status(f"[bold green]Test 3: Process by PID (PID: {my_pid})..."):
        res = await get_process_detail(pid=my_pid)
        show_llm_view(res, "Process Detail (Exact Mode)")

    # 4. 测试日志检索 (Stacktrace 保护逻辑)
    with console.status("[bold green]Test 4: System Logs (Recent Notices)..."):
        res = await get_logs(level="notice", n=10, since="1 hour ago")
        show_llm_view(res, "Log Retrieval Tool")

    # 5. 测试网络详情 (1s 速率采样逻辑)
    with console.status("[bold green]Test 5: Network Stack (Real-time Sample)..."):
        res = await get_network_detail()
        show_llm_view(res, "Network Detail Tool")

    # 6. 全量快照 (压缩逻辑)
    with console.status("[bold green]Test 6: System Snapshot (Prompt Injection Target)..."):
        res = await get_system_snapshot()
        show_llm_view(res, "Global System Snapshot")

if __name__ == "__main__":
    start_time = time.time()
    try:
        asyncio.run(run_tests())
        console.print(f"[bold green]集成测试全部完成。总耗时: {time.time() - start_time:.2f}s[/bold green]")
    except KeyboardInterrupt:
        console.print("\n[bold red]测试被中断[/bold red]")
    finally:
        # 确保异步资源有时间回收
        time.sleep(0.1)