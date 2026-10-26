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
from kama_claude.core.compact.compactor import Compactor
from kama_claude.core.config import KamaConfig
from kama_claude.core.context import ExecutionContext
from kama_claude.core.events.bus import EventBus, EventHandler
from kama_claude.core.events.writer import EventWriter
from kama_claude.core.llm.base import LLMProvider
from kama_claude.core.llm.provider import AnthropicProvider
from kama_claude.core.loop import AgentLoop
from kama_claude.core.mcp.server import McpServerManager
from kama_claude.core.memory.loader import load_context_file
from kama_claude.core.permissions.manager import PermissionManager
from kama_claude.core.runs import RUNS_DIR, new_run_id
from kama_claude.core.session.model import Session
from kama_claude.core.session.store import SessionStore
from kama_claude.core.subagent.registry import BackgroundTaskRegistry
from kama_claude.core.subagent.tool import AgentResultTool, SpawnAgentTool
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
    # 用途：保存依赖；实际组装发生在 run_and_capture，run 仅是包装。
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
        mcp_manager: McpServerManager | None = None,
    ) -> None:
        # 这里只保存依赖，不启动模型请求；运行主线入口是 run_and_capture。
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
        self._mcp_manager = mcp_manager
        # 本 Runner 内共享；Core 每条消息新建 Runner，不能跨消息查询后台结果。
        self._task_registry = BackgroundTaskRegistry()

    # 构建工具注册表，注入 TaskManager（任务工具共享同一实例）；可选注入 SpawnAgentTool
    def _build_registry(
        self,
        task_manager: TaskManager,
        *,
        session: Session | None = None,
        store: SessionStore | None = None,
        run_id: str | None = None,
        provider: LLMProvider | None = None,
        bus: EventBus | None = None,
        child_runs_dir: Path | None = None,
        session_id: str = "",
        tool_whitelist: list[str] | None = None,
    ) -> ToolRegistry:
        # 每轮新建工具表：基础工具 → 会话 note → 子任务工具 → MCP。
        # 白名单筛选注册项；未注册的名称仍由调用器报 unknown tool。
        allowed: set[str] | None = set(tool_whitelist) if tool_whitelist else None

        # 判断工具是否可注册，空白名单沿用全部可用工具。
        def _ok(name: str) -> bool:
            return allowed is None or name in allowed

        registry = ToolRegistry()
        for t in [ReadFileTool(), BashTool(), WriteFileTool(), ListDirTool()]:
            if _ok(t.name):
                registry.register(t)
        for t in [
            TaskCreateTool(task_manager),
            TaskUpdateTool(task_manager),
            TaskListTool(task_manager),
            TaskGetTool(task_manager),
        ]:
            if _ok(t.name):
                registry.register(t)
        if session is not None and store is not None and run_id is not None:
            note_tool = NoteSaveTool(store, session.id, run_id)
            if _ok(note_tool.name):
                registry.register(note_tool)
        if provider is not None and bus is not None and run_id is not None:
            runs_dir = child_runs_dir or self._runs_dir
            if _ok("spawn_agent"):
                registry.register(
                    SpawnAgentTool(
                        provider=provider,
                        parent_bus=bus,
                        parent_run_id=run_id,
                        permission_manager=self._permission_manager,
                        max_steps=self._config.agent.max_steps,
                        task_registry=self._task_registry,
                        runs_dir=runs_dir,
                        session_id=session_id,
                        depth=0,
                    )
                )
            if _ok("agent_result"):
                registry.register(AgentResultTool(self._task_registry))
        if self._mcp_manager is not None:
            for mcp_tool in self._mcp_manager.get_tools():
                if _ok(mcp_tool.name):
                    registry.register(mcp_tool)
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
        system_prompt_override: str | None = None,
        tool_whitelist: list[str] | None = None,
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

        # 全局来自用户目录，项目来自 daemon cwd；session notes 已由 Store 读取。
        global_ctx = load_context_file(Path("~/.kama/context.md").expanduser())
        project_ctx = load_context_file(Path(".kama/context.md"))

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
            global_context=global_ctx,
            project_context=project_ctx,
            system_prompt_override=system_prompt_override,
        )
        prefill_len = len(history)
        # 保存前缀长度，结束后只把本轮新增的 assistant/tool_result 追加回 thread。

        # 步骤 4：打开事件文件，登记订阅并发布开始事件。
        # async with 管理事件文件；IPC 广播器和文件 writer 订阅同一进程内总线。
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 先登记文件订阅者，再发布开始事件，确保第一条记录就能写入文件。
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

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
                session_id_str = session.id if session is not None else ""
                child_runs_dir = (
                    store.runs_dir(session.id)
                    if session is not None and store is not None
                    else self._runs_dir
                )
                registry = self._build_registry(
                    task_manager,
                    session=session,
                    store=store,
                    run_id=run_id,
                    provider=provider,
                    bus=bus,
                    child_runs_dir=child_runs_dir,
                    session_id=session_id_str,
                    tool_whitelist=tool_whitelist,
                )
                session_dir = (
                    store.session_dir(session.id)
                    if session is not None and store is not None
                    else run_path
                )
                compactor = Compactor(bus, session_dir, session_id_str)
                loop = AgentLoop(
                    provider,
                    registry,
                    bus,
                    permission_manager=self._permission_manager,
                    compactor=compactor,
                    compact_threshold=self._config.compaction.auto_threshold,
                    session_id=session_id_str,
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

            # 不能让后台任务越过本 Runner 生命周期后失去查询入口和退出清理。
            finally:
                await self._task_registry.cancel_all()

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
            # 摘要已替换历史前缀，不能再按旧长度切片；备份并整体保存。
            if context.compaction_count:
                store.write_compacted(session.id, context.messages)
            else:
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
