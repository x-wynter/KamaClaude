# ═══ 文件职责：一次 Agent 任务的准备与收尾 ═══
# 协作关系：CoreApp 创建 Runner；Runner 组装 Context、Provider、工具和事件文件，再调用 Loop。
# 阅读路线：构造依赖 → _build_registry → run_and_capture；run 只是兼容的无返回值包装。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
from kama_claude.core.task.manager import TaskManager
from kama_claude.core.tools.builtin import (
    BashTool,
    ListDirTool,
    ReadFileTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    WriteFileTool,
)
from kama_claude.core.tools.registry import ToolRegistry
from kama_claude.core.trace.provider import TracingProvider
from kama_claude.core.trace.writer import TraceWriter


# ── 函数：_now ──
# 用途：返回带 UTC 时区的时间字符串，供事件 ts 字段记录发生时间。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 类：RunOutcome ──
# 职责：把 Context 的最终状态打包给 Python 调用者；不是新增 RPC 响应。
# status 为 success/failed，result 是 end_turn 的最终文本，reason 是可选失败原因。
@dataclass
class RunOutcome:
    status: str
    result: str
    reason: str | None


# ── 类：AgentRunner ──
# 职责：组装一次任务所需对象，并负责开始/结束事件与文件生命周期。
# 字段说明：
#   _config：任务配置；_bus：可选的共享事件总线；_provider：可替换的模型实现。
#   _extra_handlers：附加事件回调；_runs_dir：运行记录根目录；_trace：可选共享 trace。
class AgentRunner:
    # ── 初始化：__init__ ──
    # 用途：保存配置和可选依赖；实际组装和执行发生在 run 中。
    def __init__(
        self,
        config: KamaConfig,
        # 星号之后的参数必须按名称传入；测试可注入假 provider，避免真实 API 调用。
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
        trace: TraceWriter | None = None,
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
        self._trace = trace

    # 构建工具注册表，注入 TaskManager（任务工具共享同一实例）
    def _build_registry(self, task_manager: TaskManager) -> ToolRegistry:
        # 四种执行工具 + 四种待办工具；注册是保存对象，不会立即调用工具。
        # 四个任务工具引用同一 Manager，所以 create 的结果立刻能被 get/list/update 读取。
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(BashTool())
        registry.register(WriteFileTool())
        registry.register(ListDirTool())
        registry.register(TaskCreateTool(task_manager))
        registry.register(TaskUpdateTool(task_manager))
        registry.register(TaskListTool(task_manager))
        registry.register(TaskGetTool(task_manager))
        return registry

    # 执行一次完整的 agent run（委托给 run_and_capture，忽略返回值）
    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        await self.run_and_capture(goal, run_id=run_id)

    # 执行 agent run 并返回 RunOutcome（含最终文字结果）
    async def run_and_capture(
        self, goal: str, *, run_id: str | None = None
    ) -> RunOutcome:
        # 步骤 1：确定 run 目录和该 run 独有的任务 Manager；目录名将 run 与 task ID 隔开。
        run_id = run_id or new_run_id()
        run_path = self._runs_dir / run_id
        # Path 的 / 拼接目录；parents=True 补齐父目录，exist_ok=True 接受已有目录。
        run_path.mkdir(parents=True, exist_ok=True)

        task_manager = TaskManager(run_path / ".tasks")

        # 步骤 2：选取事件总线并添加附加回调。
        # 与 S1 不同，允许注入 daemon 的公共总线，让广播器收到此次任务事件。
        # 不注入时仍创建独立总线，便于单元测试。
        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 步骤 3：创建本次任务独立的消息历史和状态。
        # 上下文仍按任务创建；共享总线不等于共享模型消息历史。
        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
        )

        # 步骤 4：打开事件文件，登记订阅并发布开始事件。
        # async with 管理事件文件；IPC 广播器和文件 writer 订阅同一进程内总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 先登记文件订阅者，再发布开始事件，确保第一条记录就能写入文件。
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 步骤 5：选择模型，按需包装 trace，并注册全部八种工具。
            # LLMProvider 是接口约定；测试注入假实现可真正运行 Loop/工具而不请求模型。
            provider: LLMProvider = self._provider or AnthropicProvider(
                self._config.llm.default_model
            )
            if self._trace is not None:
                provider = TracingProvider(
                    provider,
                    self._trace,
                    include_payload=self._config.trace.include_llm_payload,
                )
            registry = self._build_registry(task_manager)
            loop = AgentLoop(provider, registry, bus)

            # 步骤 6：执行循环并记录取消状态。
            # 暂存取消状态，写完结束事件并关闭文件后再向调用方抛出取消异常。
            cancelled = False
            try:
                await loop.run(context)
            # 先记录取消状态并发布 run.finished，再向上重新抛出取消异常。
            except asyncio.CancelledError:
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")

            # 步骤 7：发布任务结束事件，离开上下文关闭文件。
            await bus.publish(
                RunFinishedEvent(
                    run_id=run_id,
                    # Loop 更新 context；此事件仍不带最终 result，Python 返回值另见 RunOutcome。
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )

        # 步骤 8：把取消异常继续交给上层。
        # 保留取消语义，不能把取消悄悄当作正常返回。
        if cancelled:
            raise asyncio.CancelledError()

        # 步骤 9：非取消路径返回结果；run() 会忽略它，run_and_capture() 的调用者可以读取。
        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
