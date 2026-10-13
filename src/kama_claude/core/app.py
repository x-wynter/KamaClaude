# ═══ 文件职责：核心服务的组装与生命周期 ═══
# 协作关系：CLI/TUI → SocketServer → CoreApp → AgentRunner；事件通过共享总线回到客户端。
# 阅读路线：先读 CoreApp.run，再读三个 handler，最后读历史回放和退出信号。
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
from types import FrameType
from typing import Any

import kama_claude
from kama_claude.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PongResult,
)
from kama_claude.core.bus.envelope import EventPushEnvelope
from kama_claude.core.config import KamaConfig, get_config
from kama_claude.core.events.bus import EventBus
from kama_claude.core.logging_setup import setup_logging
from kama_claude.core.runner import AgentRunner
from kama_claude.core.runs import events_file, new_run_id
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# __name__ 是当前模块名；日志器以模块命名，便于定位消息来源。
logger = logging.getLogger(__name__)


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
#   _current_run_task：当前 Agent 的异步任务；_config：启动时加载的配置。
class CoreApp:
    # ── 初始化：__init__ ──
    # 用途：创建服务级总线和广播器，初始化计时、配置和任务槽。
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        # 总线属于 daemon，可由多次 run 共用；广播器是其中一个订阅者。
        self._bus = EventBus()
        self._broadcaster = IpcEventBroadcaster()
        self._bus.subscribe(self._broadcaster.handle)
        self._current_run_task: asyncio.Task[None] | None = None
        self._config: KamaConfig | None = None

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

    # ── 函数：_agent_run_handler ──
    # 用途：启动一次 agent run：立即返回 run_id，后台 task 执行 runner.run()
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 步骤 1：校验配置、请求参数和当前任务状态。
        # run 已加载配置才会接收命令；assert 同时帮助类型检查器排除 None。
        assert self._config is not None
        # 库函数：Pydantic 的 model_validate 校验字典并创建模型；失败时抛出 ValidationError。
        cmd = AgentRunCommand.model_validate(params)

        # S2 同时只接受一个任务；done 区分仍运行与已经结束的 Task。
        if self._current_run_task is not None and not self._current_run_task.done():
            raise RuntimeError("a run is already in progress")

        # 步骤 2：准备任务 ID 和 Runner。
        run_id = new_run_id()
        runner = AgentRunner(self._config, bus=self._bus)

        # 步骤 3：调度后台任务，立即返回受理结果。
        # create_task 调度后台协程，处理器立即返回 ID；这里没有 await 整个任务。
        # 后台任务与 TCP 处理器仍在同一个 daemon 进程、同一个事件循环中。
        self._current_run_task = asyncio.create_task(
            runner.run(cmd.goal, run_id=run_id)
        )
        return AgentRunResult(run_id=run_id)

    # ── 函数：_subscribe_handler ──
    # 用途：注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 步骤 1：校验订阅参数，取得当前客户端连接。
        cmd = EventSubscribeCommand.model_validate(params)
        # 处理器只接收 params，通过 ContextVar 取得当前请求所属连接的写流。
        writer = get_connection_writer()

        # 步骤 2：按需发送历史记录。
        replayed_count = 0
        # 先发送历史再注册实时订阅；S2 没有用序号保证两者切换时无缝且不重复。
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 步骤 3：登记实时订阅并返回订阅信息。
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

        # 步骤 2：创建服务器并注册三个命令入口。
        server = SocketServer(self._config.host, self._config.port, self._broadcaster)
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)

        # 步骤 3：启动监听并记录服务信息。
        # await 等待监听启动完成；已有连接的处理由事件循环继续调度。
        addr = await server.start()
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # 步骤 4：等待退出信号，最终停止监听。
        try:
            await _wait_for_shutdown()
        # 无论等待正常结束还是抛异常，都尝试关闭已启动的服务器。
        finally:
            logger.info("shutting down")
            await server.stop()



# ── 函数：run ──
# 用途：同步入口：启动 CoreApp 事件循环
def run() -> None:
    # 先创建 CoreApp，再取得 run 协程，最后由 asyncio.run 驱动执行。
    asyncio.run(CoreApp().run())
