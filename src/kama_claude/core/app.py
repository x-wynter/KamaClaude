# core 守护进程的生命周期：初始化 → 注册命令和事件广播 → 监听 TCP → 等待退出。
# 业务处理由 CoreApp 管理，网络收发交给 SocketServer。

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


# 注册跨平台退出信号并等待通知，结束后恢复原有处理器
async def _wait_for_shutdown() -> None:
    # 取得正在运行本协程的事件循环；此时外层 asyncio.run 已经启动循环。
    loop = asyncio.get_running_loop()
    # Event 是异步通知对象，初始未设置；set 发出通知，wait 等待通知。
    shutdown = asyncio.Event()

    # 将同步信号通知转交事件循环，唤醒正在等待的守护进程
    # 嵌套函数可访问外层的 loop 和 shutdown，这称为闭包。
    # signum 和 frame 是信号回调约定的参数；FrameType | None 表示也允许空值。
    def on_signal(signum: int, frame: FrameType | None) -> None:
        # 把 shutdown.set 这个函数交给事件循环稍后执行；这里不加括号。
        loop.call_soon_threadsafe(shutdown.set)

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


class CoreApp:
    # 初始化实例并记录单调时钟起点；self 表示当前 CoreApp 对象。
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        # 总线属于 daemon，可由多次 run 共用；广播器是其中一个订阅者。
        self._bus = EventBus()
        self._broadcaster = IpcEventBroadcaster()
        self._bus.subscribe(self._broadcaster.handle)
        self._current_run_task: asyncio.Task[None] | None = None
        self._config: KamaConfig | None = None

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # dict[str, Any] 表示键是字符串，值类型不限；get 在缺少 client 时给默认值。
        # 当前处理器没有使用 PingCommand 对 params 做业务模型校验。
        client = params.get("client", "unknown")
        # %s 的参数交给日志库格式化，debug 是否输出由日志级别控制。
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=kama_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            # 生成带 UTC 时区的当前时间，再用 isoformat 转成 ISO 8601 字符串。
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 启动一次 agent run：立即返回 run_id，后台 task 执行 runner.run()
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # run 已加载配置才会接收命令；assert 同时帮助类型检查器排除 None。
        assert self._config is not None
        cmd = AgentRunCommand.model_validate(params)

        # S2 同时只接受一个任务；done 区分仍运行与已经结束的 Task。
        if self._current_run_task is not None and not self._current_run_task.done():
            raise RuntimeError("a run is already in progress")

        run_id = new_run_id()
        runner = AgentRunner(self._config, bus=self._bus)
        # create_task 调度后台协程，处理器立即返回 ID；这里没有 await 整个任务。
        # 后台任务与 TCP 处理器仍在同一个 daemon 进程、同一个事件循环中。
        self._current_run_task = asyncio.create_task(
            runner.run(cmd.goal, run_id=run_id)
        )
        return AgentRunResult(run_id=run_id)

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        cmd = EventSubscribeCommand.model_validate(params)
        # 处理器只接收 params，通过 ContextVar 取得当前请求所属连接的写流。
        writer = get_connection_writer()

        replayed_count = 0
        # 先发送历史再注册实时订阅；S2 没有用序号保证两者切换时无缝且不重复。
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        path = events_file(run_id)
        if not path.exists():
            return 0

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

        if count:
            await writer.drain()
        return count

    # 启动守护进程：加载配置、初始化日志、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        self._start_time = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)

        server = SocketServer(self._config.host, self._config.port, self._broadcaster)
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)

        # await 等待监听启动完成；已有连接的处理由事件循环继续调度。
        addr = await server.start()
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        try:
            await _wait_for_shutdown()
        # 无论等待正常结束还是抛异常，都尝试关闭已启动的服务器。
        finally:
            logger.info("shutting down")
            await server.stop()



# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    # 先创建 CoreApp，再取得 run 协程，最后由 asyncio.run 驱动执行。
    asyncio.run(CoreApp().run())
