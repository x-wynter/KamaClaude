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
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        # 这里只保存依赖，不启动模型请求；真正执行入口是下面的 run。
        # self._config 的下划线表示内部属性约定，不是 Python 强制私有。
        self._config = config
        self._provider = provider
        # extra_handlers 可传 None；or [] 给本实例一个空列表。
        # 若传入非空列表则保存原列表引用，不会自动复制其中的回调。
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR

    # 执行一次完整的 agent run：生成 run_id、接线事件总线、驱动 AgentLoop
    async def run(self, goal: str) -> None:
        # 每次调用 run 都生成新的任务 ID；同一个 Runner 可以顺序执行多次任务。
        run_id = new_run_id()
        run_path = self._runs_dir / run_id
        # Path 的 / 拼接目录；parents=True 补齐父目录，exist_ok=True 接受已有目录。
        run_path.mkdir(parents=True, exist_ok=True)

        # 每次任务使用独立总线，避免不同任务的打印器和文件订阅混在一起。
        bus = EventBus()
        # h 是异步函数对象，如 printer.handle；subscribe 只是保存它，还没有调用。
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 未注入 provider 时才创建真实客户端；or 返回第一个真值对象。
        provider = self._provider or AnthropicProvider(self._config.llm.default_model)
        # S1 只注册 read_file；模型只能看到下面注册表导出的工具说明。
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        # 这是项目的业务循环对象，不是 asyncio 的底层事件循环。
        # 把同一 provider、registry 和 bus 交给它，后续由它协调调用。
        loop = AgentLoop(provider, registry, bus)

        # 构造上下文时 __post_init__ 自动把 goal 放入第一条 user 消息。
        # max_steps 来自配置；计数和状态由 AgentLoop 修改。
        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
        )

        # async with 进入时打开文件，退出时关闭；writer 与终端打印器订阅同一总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 先登记文件订阅者，再发布开始事件，确保第一条记录就能写入文件。
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 用局部标记把取消异常延后到文件关闭之后重新抛出。
            # 这里专门处理取消，并不是兜底捕获所有异常。
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
