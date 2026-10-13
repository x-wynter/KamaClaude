# ═══ 文件职责：提交目标并在命令行显示执行过程 ═══
# 协作关系：本进程只负责通信和输出；Agent 实际运行在 Core 进程中。
# 阅读路线：先读 cmd_run 和 _run_async 的主流程，再看 StdoutPrinter 如何显示事件。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from kama_claude.core.config import KamaConfig
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# ── 类：StdoutPrinter ──
# 职责：把事件字典转换为终端文字，并维护换行与计时状态。
# 字段说明：
#   _inline：上一段模型文本是否尚未换行；_run_start：任务开始的计时读数。
class StdoutPrinter:
    # ── 初始化：__init__ ──
    # 用途：接收 dict 格式的事件并将运行进度格式化打印到终端
    def __init__(self) -> None:
        # 实例属性记住是否正在输出同一行模型文本；不同打印器实例分别维护状态。
        self._inline = False  # True while LLM tokens are mid-line
        self._run_start: float = 0.0

    # ── 函数：_ensure_newline ──
    # 用途：若上一段是无换行的模型流输出，先补换行再打印后续状态。
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # ── 函数：handle ──
    # 用途：根据事件 type 字段分发并格式化打印到 stdout/stderr
    async def handle(self, event: dict[str, Any]) -> None:
        # JSON 反序列化得到 dict，因此按 type 字符串分支，不能再用 isinstance 事件类。
        t = event.get("type", "")

        if t == "run.started":
            self._run_start = time.monotonic()
            print(f"[run] {event.get('run_id', '')}")

        elif t == "step.started":
            self._ensure_newline()
            print(f"[step {event.get('step')}] planning...")

        elif t == "llm.token":
            # end 不换行，flush 立即刷新终端缓冲；连续文本片段拼成同一行。
            print(event.get("token", ""), end="", flush=True)
            self._inline = True

        elif t == "tool.call_started":
            self._ensure_newline()
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        elif t == "tool.call_finished":
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        elif t == "tool.call_failed":
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        elif t == "step.finished":
            self._ensure_newline()
            print(f"[step {event.get('step')}] done")

        elif t == "run.finished":
            self._ensure_newline()
            # elapsed 的单位为秒；下面 f 字符串中的 :.1f 保留一位小数。
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {elapsed:.1f}s")


# ── 函数：_run_async ──
# 用途：异步核心：连接 daemon，订阅事件，触发 run，等待 run.finished
# 输入：目标文本和连接配置；输出：成功返回 0，业务失败返回 1。
async def _run_async(goal: str, config: KamaConfig) -> int:
    # 步骤 1：创建客户端并连接 Core。
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    # 步骤 2：准备输出器、完成通知和退出码。
    printer = StdoutPrinter()
    # Event 是一次完成通知；收到 run.finished 时 set，主协程在 wait 处恢复。
    finished = asyncio.Event()
    exit_code = 0

    # 步骤 3：定义回调：打印事件并识别任务结束。
    # ── 函数：on_event ──
    async def on_event(event: dict[str, Any]) -> None:
        # nonlocal 让赋值修改外层局部变量；没有它会创建回调自己的 exit_code。
        nonlocal exit_code
        await printer.handle(event)
        if event.get("type") == "run.finished":
            if event.get("status") != "success":
                exit_code = 1
            finished.set()

    # 步骤 4：登记回调并启动独立接收任务。
    client.on_event(on_event)
    # 接收循环与当前协程协作运行，否则 send_command 等不到 Future 被填入结果。
    loop_task = asyncio.create_task(client.run_event_loop())

    # 步骤 5：先订阅事件，再提交任务目标。
    try:
        # 先订阅再提交目标，否则可能错过任务很快发布的 run.started/finished。
        # 当前使用 global 订阅，事件回调没有按返回的 run_id 再过滤。
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage"],
                "scope": "global",
            },
        )
        await client.send_command("agent.run", {"goal": goal})
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        loop_task.cancel()
        await client.close()
        return 1

    # 步骤 6：等待任务完成事件，而非仅等待提交响应。
    # agent.run 的 RPC 响应只表示任务已提交；真正完成由 run.finished 通知。
    # 当前此处未与断线任务竞争等待，运行途中断线可能一直等到用户 Ctrl+C。
    await finished.wait()

    # 步骤 7：停止接收循环，关闭连接并返回退出码。
    # cancel 只请求取消；await 让任务有机会执行 finally 并完成退出。
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    await client.close()
    return exit_code


# ── 函数：cmd_run ──
# 用途：执行 kama run --goal "..." 命令
def cmd_run(goal: str, config: KamaConfig) -> None:
    try:
        exit_code = asyncio.run(_run_async(goal, config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
