# ═══ 文件职责：一次 Agent 任务的准备与收尾 ═══
# 协作关系：CoreApp 的会话工厂创建 Runner；Runner 组装 Context/工具/Provider 并调用 Loop。
# 阅读路线：构造依赖 → _build_registry → run_and_capture；run 只是兼容的无返回值包装。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import logging
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
from kama_claude.core.permissions.manager import PermissionManager
from kama_claude.core.runs import RUNS_DIR, new_run_id
from kama_claude.core.session.model import Session
from kama_claude.core.session.store import SessionStore
from kama_claude.core.task.manager import TaskManager
from kama_claude.core.tools.builtin import (
    BashTool,
    ListDirTool,
    NoteSaveTool,
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
        permission_manager: PermissionManager | None = None,
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
        # Core 创建的共享权限管理器；直接 Python 调用可不传，不由 Runner 每轮重建。
        self._permission_manager = permission_manager

    # 构建工具注册表，注入 TaskManager（任务工具共享同一实例）
    def _build_registry(
        self,
        task_manager: TaskManager,
        *,
        session: Session | None = None,
        store: SessionStore | None = None,
        run_id: str | None = None,
    ) -> ToolRegistry:
        # 四种执行工具和四种待办工具始终注册；会话路径额外注册 note_save，共九种。
        # 四个 task 工具共享本轮 Manager；注册只保存对象，不会执行工具。
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(BashTool())
        registry.register(WriteFileTool())
        registry.register(ListDirTool())
        registry.register(TaskCreateTool(task_manager))
        registry.register(TaskUpdateTool(task_manager))
        registry.register(TaskListTool(task_manager))
        registry.register(TaskGetTool(task_manager))
        if session is not None and store is not None and run_id is not None:
            registry.register(NoteSaveTool(store, session.id, run_id))
        return registry

    # 执行一次完整的 agent run（委托给 run_and_capture，忽略返回值）
    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        await self.run_and_capture(goal, run_id=run_id)

    # 执行 agent run 并返回 RunOutcome（含最终文字结果）
    async def run_and_capture(
        self,
        goal: str,
        *,
        run_id: str | None = None,
        session: Session | None = None,
        store: SessionStore | None = None,
    ) -> RunOutcome:
        # 步骤 1：确定 run 目录和该 run 独有的任务 Manager；目录名将 run 与 task ID 隔开。
        run_id = run_id or new_run_id()
        if session is not None and store is not None:
            # Manager 已追加本轮 user；读完整历史和笔记，不能再次插入 goal。
            run_path = store.runs_dir(session.id) / run_id
            history = store.read_messages(session.id)
            notes = store.read_notes(session.id)
        else:
            run_path = self._runs_dir / run_id
            history = [{"role": "user", "content": goal}]
            notes = ""
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
            prefill_messages=history,
            session_notes=notes,
        )
        prefill_len = len(history)
        # 保存前缀长度，结束后只把本轮新增的 assistant/tool_result 追加回 thread。

        # 步骤 4：打开事件文件，登记订阅并发布开始事件。
        # async with 管理事件文件；IPC 广播器和文件 writer 订阅同一进程内总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 先登记文件订阅者，再发布开始事件，确保第一条记录就能写入文件。
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 步骤 5：把会话信息绑定到 note_save；无会话的直接调用仍保留八工具。
            registry = self._build_registry(
                task_manager,
                session=session,
                store=store,
                run_id=run_id,
            )

            # 步骤 6：执行循环并记录取消状态。
            # 暂存取消状态，写完结束事件并关闭文件后再向调用方抛出取消异常。
            cancelled = False
            try:
                provider: LLMProvider = self._provider or AnthropicProvider(
                    self._config.llm.default_model
                )
                if self._trace is not None:
                    provider = TracingProvider(
                        provider,
                        self._trace,
                        include_payload=self._config.trace.include_llm_payload,
                    )
                loop = AgentLoop(
                    provider, registry, bus,
                    permission_manager=self._permission_manager,
                    session_id=session.id if session is not None else "",
                )
                await loop.run(context)
            # 先记录取消状态并发布 run.finished，再向上重新抛出取消异常。
            except asyncio.CancelledError:
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")
            except Exception:
                logging.getLogger(__name__).exception(
                    "agent run failed run_id=%s step=%d", run_id, context.step
                )
                if not context.is_done():
                    context.mark_failed("llm_error")

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

        if session is not None and store is not None:
            # 步骤 8：事件文件先关闭，再保存本轮模型消息；两种文件的内容不同。
            store.append_messages(session.id, context.messages[prefill_len:], run_id=run_id)

        # 步骤 9：把取消异常继续交给上层。
        # 保留取消语义，不能把取消悄悄当作正常返回。
        if cancelled:
            raise asyncio.CancelledError()

        # 步骤 10：非取消路径返回结果；run() 忽略它，SessionManager 当前也不检查其 status。
        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
