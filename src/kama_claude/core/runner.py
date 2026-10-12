# 一次任务的组装入口：创建上下文、模型、工具与事件订阅，再驱动 AgentLoop。

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from kama_claude.core.bus.events import RunFinishedEvent, RunStartedEvent
from kama_claude.core.config import KamaConfig
from kama_claude.core.context import ExecutionContext
from kama_claude.core.events.bus import EventBus, EventHandler
from kama_claude.core.events.writer import EventWriter
from kama_claude.core.llm.base import LLMProvider
from kama_claude.core.llm.provider import AnthropicProvider
from kama_claude.core.loop import AgentLoop
from kama_claude.core.runs import RUNS_DIR, new_run_id
from kama_claude.core.tools.builtin.read_file import ReadFileTool
from kama_claude.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentRunner:
    # 组装所有运行时依赖，准备执行一次完整的 agent run
    def __init__(
        self,
        config: KamaConfig,
        # 星号之后的参数必须按名称传入；测试可注入假 provider，避免真实 API 调用。
        *,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR

    # 执行一次完整的 agent run：生成 run_id、接线事件总线、驱动 AgentLoop
    async def run(self, goal: str) -> None:
        run_id = new_run_id()
        run_path = self._runs_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)

        bus = EventBus()
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 未注入 provider 时才创建真实客户端；or 返回第一个真值对象。
        provider = self._provider or AnthropicProvider(self._config.llm.default_model)
        # S1 只注册 read_file；模型只能看到下面注册表导出的工具说明。
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        loop = AgentLoop(provider, registry, bus)

        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
        )

        # async with 进入时打开文件，退出时关闭；writer 与终端打印器订阅同一总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            cancelled = False
            try:
                await loop.run(context)
            # 先记录取消状态并发布 run.finished，再向上重新抛出取消异常。
            except asyncio.CancelledError:
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")

            await bus.publish(
                RunFinishedEvent(
                    run_id=run_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )

        # 保留取消语义，不能把取消悄悄当作正常返回。
        if cancelled:
            raise asyncio.CancelledError()
