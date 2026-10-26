# ═══ 文件职责：核心服务的组装与生命周期 ═══
# 协作关系：CLI/TUI → SocketServer → CoreApp → AgentRunner；事件通过共享总线回到客户端。
# 阅读路线：先读 run 接线，再读 agent.run/session.* handler，最后看回放和退出信号。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import logging
import signal
import time
from contextlib import ExitStack
from datetime import UTC
from pathlib import Path
from types import FrameType
from typing import Any

from pydantic import BaseModel

import kama_claude
from kama_claude.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PermissionRespondCommand,
    PermissionRespondResult,
    PongResult,
    SessionCloseCommand,
    SessionCloseResult,
    SessionCompactCommand,
    SessionCompactResult,
    SessionCreateCommand,
    SessionCreateResult,
    SessionGetHistoryCommand,
    SessionGetHistoryResult,
    SessionSendMessageCommand,
    SessionSendMessageResult,
)
from kama_claude.core.bus.envelope import EventPushEnvelope
from kama_claude.core.config import KamaConfig, get_config
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.provider import AnthropicProvider
from kama_claude.core.logging_setup import setup_logging
from kama_claude.core.mcp.server import McpServerManager
from kama_claude.core.permissions.manager import PermissionManager
from kama_claude.core.permissions.storage import load_policy_file
from kama_claude.core.runner import AgentRunner
from kama_claude.core.runs import events_file, new_run_id
from kama_claude.core.session import SessionManager, SessionStore
from kama_claude.core.trace.record import TraceRecord
from kama_claude.core.trace.writer import TraceWriter
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# __name__ 是当前模块名；日志器以模块命名，便于定位消息来源。
logger = logging.getLogger(__name__)


# 返回 trace 使用的 UTC 时间字符串；计时仍使用 time.monotonic。
def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


# ── 函数：_wait_for_shutdown ──
# 用途：注册跨平台退出信号并等待通知，结束后恢复原有处理器
async def _wait_for_shutdown() -> None:
    # 步骤 1：取得事件循环并创建退出通知。
    # 取得正在运行本协程的事件循环；此时外层 asyncio.run 已经启动循环。
    loop = asyncio.get_running_loop()
    # Event 是异步通知对象，初始未设置；set 发出通知，wait 等待通知。
    shutdown = asyncio.Event()

    # 步骤 2：定义同步信号到异步通知的桥接回调。
    # ── 函数：on_signal ──
    # 用途：将同步信号通知转交事件循环，唤醒正在等待的守护进程
    # 嵌套函数可访问外层的 loop 和 shutdown，这称为闭包。
    # signum 和 frame 是信号回调约定的参数；FrameType | None 表示也允许空值。
    def on_signal(signum: int, frame: FrameType | None) -> None:
        # 把 shutdown.set 这个函数交给事件循环稍后执行；这里不加括号。
        loop.call_soon_threadsafe(shutdown.set)

    # 步骤 3：注册跨平台信号处理，并等待退出后恢复现场。
    # ExitStack 统一管理清理回调；退出 with 时按注册顺序的逆序执行。
    with ExitStack() as cleanup:
        # SIGINT 常对应 Ctrl+C；SIGTERM 表示终止请求，实际支持方式与平台有关。
        for sig in (signal.SIGINT, signal.SIGTERM):
            # 保存原信号处理器，退出等待后恢复，避免永久改变进程级设置。
            previous = signal.getsignal(sig)
            try:
                # 优先让事件循环处理信号；不支持该接口的平台会抛出 NotImplementedError。
                loop.add_signal_handler(sig, shutdown.set)
            except NotImplementedError:
                # 回退到标准库同步信号处理，再由 on_signal 将通知转给事件循环。
                signal.signal(sig, on_signal)
                # callback 保存函数和参数，在清理时调用 signal.signal(sig, previous)。
                cleanup.callback(signal.signal, sig, previous)
            # try 的 else 仅在 try 没有抛异常时执行，不是对应普通 if 的分支。
            else:
                cleanup.callback(signal.signal, sig, previous)
                # 后注册、先执行：先移除事件循环的处理器，再恢复原处理器。
                cleanup.callback(loop.remove_signal_handler, sig)
        # 等待期间让出执行机会；收到退出信号后继续，离开 with 执行清理。
        await shutdown.wait()


# ── 类：CoreApp ──
# 职责：保存服务级依赖，并把网络命令连接到任务执行和事件订阅。
# 字段说明：
#   _start_time：服务启动的单调时钟读数，用于计算运行时长。
#   _bus / _broadcaster：进程内事件分发器 / 向客户端发送事件的订阅者。
#   _running_runs：所有尚未完成的 asyncio.Task；完成回调自动移除，退出时统一取消。
#   _trace：可选的 daemon 级记录器；_config：启动时加载的配置。
class CoreApp:
    # ── 初始化：__init__ ──
    # 用途：先创建总线与任务集合；广播器和 trace 要等 run 加载配置后创建。
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        # 总线属于 daemon，可由多次 run 共用；广播器是其中一个订阅者。
        self._bus = EventBus()
        self._broadcaster: IpcEventBroadcaster | None = None
        self._trace: TraceWriter | None = None
        self._config: KamaConfig | None = None
        self._running_runs: set[asyncio.Task[Any]] = set()
        self._connection_sessions: dict[asyncio.StreamWriter, set[str]] = {}
        self._sessions: SessionManager | None = None
        # Core 启动时创建的权限管理器跨 run/session 复用，独立于会话锁。
        self._permission_manager: PermissionManager | None = None
        self._mcp_manager: McpServerManager | None = None

    # 记录提交会话的连接；直接调用 handler 的测试可以没有网络上下文。
    def _remember_session(self, sid: str) -> None:
        try:
            writer = get_connection_writer()
        except LookupError:
            return
        self._connection_sessions.setdefault(writer, set()).add(sid)

    # 断连只结束已有审批等待，不把断连等同于取消整轮模型运行。
    def _on_disconnect(self, writer: asyncio.StreamWriter) -> None:
        for sid in self._connection_sessions.pop(writer, set()):
            if self._permission_manager is not None:
                self._permission_manager.cancel_session(sid)

    # ── 函数：_ping_handler ──
    # 用途：处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # dict[str, Any] 表示键是字符串，值类型不限；get 在缺少 client 时给默认值。
        # 当前处理器没有使用 PingCommand 对 params 做业务模型校验。
        client = params.get("client", "unknown")
        # %s 的参数交给日志库格式化，debug 是否输出由日志级别控制。
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=kama_claude.__version__,
            # 库函数：time.monotonic 返回不受系统校时影响的秒数；差值乘 1000 得到毫秒。
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            # 生成带 UTC 时区的当前时间，再用 isoformat 转成 ISO 8601 字符串。
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 EventBus 业务事件转换为 layer=event 记录；这里只入队，不直接写磁盘。
    async def _trace_event_handler(self, event: BaseModel) -> None:
        assert self._trace is not None
        event_dict = event.model_dump()
        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE",
                layer="event",
                kind="event",
                run_id=event_dict.get("run_id"),
                data=event_dict,
            )
        )

    # 启动一次 agent run：异步创建 AgentRunner 并立即返回 run_id
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 步骤 1：兼容旧入口，但 S4 内部先创建 one_shot 会话。
        assert self._sessions is not None
        cmd = AgentRunCommand.model_validate(params)
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        self._remember_session(session.id)
        run_id = new_run_id()
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, run_id=run_id)
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        return AgentRunResult(run_id=run_id)

    # 创建 chat 或 one_shot session，并返回 session_id
    async def _session_create_handler(self, params: dict[str, Any]) -> SessionCreateResult:
        assert self._sessions is not None
        cmd = SessionCreateCommand.model_validate(params)
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        self._remember_session(session.id)
        return SessionCreateResult(session_id=session.id, status=session.status)

    # 向 session 发送一条用户消息并同步等待对应 run 完成
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        assert self._sessions is not None
        cmd = SessionSendMessageCommand.model_validate(params)
        self._remember_session(cmd.session_id)
        # S4 的 chat 也纳入退出回收；await 保持 RPC 等待本轮完成的官方语义。
        run_task = asyncio.create_task(self._sessions.send_message(cmd.session_id, cmd.content))
        self._running_runs.add(run_task)
        # 步骤2：保存强引用，完成回调移除；此 chat handler 仍等待本轮结束。
        run_task.add_done_callback(self._running_runs.discard)
        run_id = await run_task
        return SessionSendMessageResult(run_id=run_id)

    # 返回 session 的完整 Anthropic messages 历史
    async def _session_history_handler(self, params: dict[str, Any]) -> SessionGetHistoryResult:
        assert self._sessions is not None
        cmd = SessionGetHistoryCommand.model_validate(params)
        messages = await self._sessions.get_history(cmd.session_id)
        return SessionGetHistoryResult(messages=messages)

    # 接收客户端权限审批响应，resolve 对应挂起的 Future
    async def _permission_respond_handler(self, params: dict[str, Any]) -> PermissionRespondResult:
        cmd = PermissionRespondCommand.model_validate(params)
        logger.info(
            "permission.respond received tool_use_id=%s decision=%s", cmd.tool_use_id, cmd.decision
        )
        if self._permission_manager is None:
            logger.error("permission.respond: PermissionManager not initialized")
            return PermissionRespondResult()
        self._permission_manager.respond(cmd.tool_use_id, cmd.decision)
        return PermissionRespondResult()

    # 手动压缩 session thread，将摘要持久化写入 thread.jsonl
    async def _session_compact_handler(self, params: dict[str, Any]) -> SessionCompactResult:
        assert self._sessions is not None
        cmd = SessionCompactCommand.model_validate(params)
        result = await self._sessions.compact(cmd.session_id, cmd.focus)
        return result  # type: ignore[no-any-return]

    # 关闭 session 并返回 closed 状态
    async def _session_close_handler(self, params: dict[str, Any]) -> SessionCloseResult:
        assert self._sessions is not None
        cmd = SessionCloseCommand.model_validate(params)
        if self._permission_manager is not None:
            self._permission_manager.cancel_session(cmd.session_id, "session_closed")
        await self._sessions.close(cmd.session_id)
        return SessionCloseResult(status="closed")

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 步骤 1：校验订阅参数，取得当前客户端连接。
        cmd = EventSubscribeCommand.model_validate(params)
        # 处理器只接收 params，通过 ContextVar 取得当前请求所属连接的写流。
        writer = get_connection_writer()

        # 步骤 2：按需发送历史记录。
        replayed_count = 0
        # 先发送历史再注册实时订阅；当前没有序号保证切换时无缝且不重复。
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(cmd.replay_from_run, writer, cmd.topics)

        # 步骤 3：登记实时订阅并返回订阅信息。
        assert self._broadcaster is not None
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # ── 函数：_replay_events ──
    # 用途：从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        # 步骤 1：定位历史文件；不存在就返回零条。
        path = events_file(run_id)
        if not path.exists():
            for candidate in (
                Path("~/.kama/sessions").expanduser().glob(f"*/runs/{run_id}/events.jsonl")
            ):
                path = candidate
                break
        if not path.exists():
            return 0

        # 步骤 2：逐行解析 JSON，并按主题筛选后写入连接。
        count = 0
        # 文件由 EventWriter 以 UTF-8 写入，读取必须使用同一编码，尤其是 Windows。
        # splitlines 去掉换行；json.loads 将每行恢复为普通事件字典。
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type", "")
            # 历史回放在这里按 topic 过滤；实时广播还会检查 scope。
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1

        # 步骤 3：等待发送缓冲压力缓解，返回回放条数。
        if count:
            await writer.drain()
        return count

    # ── 函数：run ──
    # 用途：启动守护进程：加载配置、初始化日志、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        # 步骤 1：加载配置并初始化日志。
        self._start_time = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)

        # 步骤 2：先启动 trace，再接线广播器/会话工厂，最后注册九个 RPC 入口。
        # Path.expanduser 将 ~ 展开到用户目录；trace=None 表示禁用所有这些采集点。
        if self._config.trace.enabled:
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = TraceWriter(trace_path)
            await self._trace.start()
            self._bus.subscribe(self._trace_event_handler)

        policy_file = Path("~/.kama/policy.toml").expanduser()
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )
        logger.info(
            "permission manager: timeout_s=%.1f  persistent=%d entries",
            self._config.permission.timeout_s,
            len(load_policy_file(policy_file)),
        )

        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        self._bus.subscribe(self._broadcaster.handle)
        sessions_root = Path("~/.kama/sessions").expanduser()
        store = SessionStore(sessions_root)
        assert self._config is not None
        self._mcp_manager = McpServerManager()
        server: SocketServer | None = None
        try:
            if self._config.mcp.servers:
                logger.info("mcp: starting %d server(s)", len(self._config.mcp.servers))
                await self._mcp_manager.start_all(self._config.mcp.servers)

            self._sessions = SessionManager(
                # lambda 是延迟调用的工厂；每条消息运行时才创建独立 Runner。
                store,
                runner_factory=lambda: AgentRunner(
                    self._config,  # type: ignore[arg-type]
                    bus=self._bus,
                    trace=self._trace,
                    permission_manager=self._permission_manager,
                    mcp_manager=self._mcp_manager,
                ),
                bus=self._bus,
                # 延迟创建压缩 Provider，保持无凭据时 ping/离线 daemon 仍可启动。
                provider_factory=lambda: AnthropicProvider(self._config.llm.default_model),  # type: ignore[union-attr]
            )

            server = SocketServer(
                self._config.host,
                self._config.port,
                self._broadcaster,
                trace=self._trace,
                on_disconnect=self._on_disconnect,
            )
            server.register("core.ping", self._ping_handler)
            server.register("agent.run", self._agent_run_handler)
            server.register("event.subscribe", self._subscribe_handler)
            server.register("session.create", self._session_create_handler)
            server.register("session.send_message", self._session_send_handler)
            server.register("session.get_history", self._session_history_handler)
            server.register("session.close", self._session_close_handler)
            server.register("permission.respond", self._permission_respond_handler)
            server.register("session.compact", self._session_compact_handler)

            # 步骤 3：启动监听并记录服务信息。
            # await 等待监听启动完成；已有连接的处理由事件循环继续调度。
            addr = await server.start()
            logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
            logger.info("config: %s", self._config)

            # 步骤 4：等待跨平台退出信号；取消所有 run 后再关闭网络和 trace。
            await _wait_for_shutdown()
        finally:
            logger.info("shutting down")
            # list 创建快照，避免完成回调 discard 改变正在遍历的集合。
            pending = list(self._running_runs)
            for run_task in pending:
                run_task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            try:
                if server is not None:
                    await server.stop()
            finally:
                try:
                    await self._mcp_manager.stop_all()
                finally:
                    if self._trace is not None:
                        await self._trace.stop()


# ── 函数：run ──
# 用途：同步入口：启动 CoreApp 事件循环
def run() -> None:
    # 先创建 CoreApp，再取得 run 协程，最后由 asyncio.run 驱动执行。
    asyncio.run(CoreApp().run())
