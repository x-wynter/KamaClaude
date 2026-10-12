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
        self._inline = False  # True while LLM tokens are mid-line
        self._run_start: float = 0.0

    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    async def handle(self, event: BaseModel) -> None:
        # isinstance 按事件模型类型分支，同一个订阅函数可接收多种事件。
        if isinstance(event, RunStartedEvent):
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
                file=sys.stderr,
            )

        elif isinstance(event, StepFinishedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] done")

        elif isinstance(event, RunFinishedEvent):
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.status}  {event.steps} steps  {elapsed:.1f}s")


# 执行 kama run --goal "..." 命令
def cmd_run(goal: str, config: KamaConfig) -> None:
    printer = StdoutPrinter()
    # 传入绑定方法而非调用结果；Runner 将它订阅到本次任务的事件总线。
    runner = AgentRunner(config, extra_handlers=[printer.handle])
    try:
        asyncio.run(runner.run(goal))
    # Ctrl+C 在同步入口表现为 KeyboardInterrupt；130 是这里约定的退出码。
    except KeyboardInterrupt:
        sys.exit(130)
