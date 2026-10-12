# S1 的 run 命令直接在当前 CLI 进程执行 AgentRunner，不经过 core 的 TCP 服务。

from __future__ import annotations

import asyncio
import json
import sys
import time

from pydantic import BaseModel

from kama_claude.core.bus.events import (
    LlmTokenEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from kama_claude.core.config import KamaConfig
from kama_claude.core.runner import AgentRunner


class StdoutPrinter:
    # 订阅事件总线并将运行进度格式化打印到终端
    def __init__(self) -> None:
        # 实例属性记住是否正在输出同一行模型文本；不同打印器实例分别维护状态。
        self._inline = False  # True while LLM tokens are mid-line
        self._run_start: float = 0.0

    # 若上一段是无换行的模型流输出，先补换行再打印后续状态。
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 事件订阅回调；总线传入 BaseModel，下面再按具体子类读取对应字段。
    # 函数虽然标为 async，内部 print 仍是同步终端输出。
    async def handle(self, event: BaseModel) -> None:
        # isinstance 按事件模型类型分支，同一个订阅函数可接收多种事件。
        if isinstance(event, RunStartedEvent):
            # 收到任务开始事件时记录单调时间，任务结束时用差值显示持续秒数。
            self._run_start = time.monotonic()
            print(f"[run] {event.run_id}")

        elif isinstance(event, StepStartedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] planning...")

        elif isinstance(event, LlmTokenEvent):
            # end="" 不自动换行，flush=True 及时刷新终端，形成流式输出。
            print(event.token, end="", flush=True)
            self._inline = True

        elif isinstance(event, ToolCallStartedEvent):
            self._ensure_newline()
            # ensure_ascii=False 保留可读中文；结果仍然是 JSON 字符串。
            params_str = json.dumps(event.params, ensure_ascii=False)
            print(f"[tool] {event.tool_name} {params_str}")

        elif isinstance(event, ToolCallFinishedEvent):
            print(f"[tool] {event.tool_name} ✓  {event.elapsed_ms}ms")

        elif isinstance(event, ToolCallFailedEvent):
            print(
                f"[tool] {event.tool_name} ✗  {event.error_message}",
                # 把工具失败信息送到标准错误流；重定向 stdout 时可与正常输出分开。
                file=sys.stderr,
            )

        elif isinstance(event, StepFinishedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] done")

        elif isinstance(event, RunFinishedEvent):
            self._ensure_newline()
            # elapsed 的单位为秒；下面 f 字符串中的 :.1f 保留一位小数。
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.status}  {event.steps} steps  {elapsed:.1f}s")


# 执行 kama run --goal "..." 命令
def cmd_run(goal: str, config: KamaConfig) -> None:
    # 每次命令创建新的打印器，避免继承前一次任务的输出状态。
    printer = StdoutPrinter()
    # 传入绑定方法而非调用结果；Runner 将它订阅到本次任务的事件总线。
    runner = AgentRunner(config, extra_handlers=[printer.handle])
    try:
        # runner.run(goal) 先生成协程对象，再由 asyncio.run 创建事件循环并驱动执行。
        asyncio.run(runner.run(goal))
    # Ctrl+C 在同步入口表现为 KeyboardInterrupt；130 是这里约定的退出码。
    except KeyboardInterrupt:
        sys.exit(130)
