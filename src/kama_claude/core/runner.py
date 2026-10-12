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


# 返回带 UTC 时区的时间字符串，供事件 ts 字段记录发生时间。
def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentRunner:
    # 组装所有运行时依赖，准备执行一次完整的 agent run
    def __init__(
        self,
        config: KamaConfig,
        # 星号之后的参数必须按名称传入；测试可注入假 provider，避免真实 API 调用。
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        # 这里只保存依赖，不启动模型请求；真正执行入口是下面的 run。
        # self._config 的下划线表示内部属性约定，不是 Python 强制私有。
        self._config = config
        self._bus = bus
        self._provider = provider
        # extra_handlers 可传 None；or [] 给本实例一个空列表。
        # 若传入非空列表则保存原列表引用，不会自动复制其中的回调。
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR

    # 执行一次完整的 agent run：接线事件总线、驱动 AgentLoop、写 events.jsonl
    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        # daemon 先生成 ID 再调度本协程；独立使用 Runner 时仍可自动生成 ID。
        run_id = run_id or new_run_id()
        run_path = self._runs_dir / run_id
        # Path 的 / 拼接目录；parents=True 补齐父目录，exist_ok=True 接受已有目录。
        run_path.mkdir(parents=True, exist_ok=True)

        # 与 S1 不同，允许注入 daemon 的公共总线，让广播器收到此次任务事件。
        # 不注入时仍创建独立总线，便于单元测试。
        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 上下文仍按任务创建；共享总线不等于共享模型消息历史。
        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
        )

        # async with 管理事件文件；IPC 广播器和文件 writer 订阅同一进程内总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 先登记文件订阅者，再发布开始事件，确保第一条记录就能写入文件。
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # S2 将依赖创建放在 run.started 之后；测试可以先观察到任务已启动。
            # 这里只注册 read_file；AgentLoop 仍沿用 S1 的模型—工具循环。
            provider = self._provider or AnthropicProvider(self._config.llm.default_model)
            registry = ToolRegistry()
            registry.register(ReadFileTool())
            loop = AgentLoop(provider, registry, bus)

            # 暂存取消状态，写完结束事件并关闭文件后再向调用方抛出取消异常。
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
                    # Loop 通过修改 context 传回结果；run 本身没有返回最终回答字符串。
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )

        # 保留取消语义，不能把取消悄悄当作正常返回。
        if cancelled:
            raise asyncio.CancelledError()
