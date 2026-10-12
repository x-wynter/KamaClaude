# core 守护进程的生命周期：初始化 → 注册 ping 处理器 → 监听 TCP → 等待退出。
# 业务处理由 CoreApp 管理，网络收发交给 SocketServer。

from __future__ import annotations

import asyncio
import datetime
import logging
import signal
import time
from contextlib import ExitStack
from types import FrameType
from typing import Any

import kama_claude
from kama_claude.core.bus.commands import PongResult
from kama_claude.core.config import get_config
from kama_claude.core.logging_setup import setup_logging
from kama_claude.core.transport.socket_server import SocketServer

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

    # 启动守护进程：加载配置、初始化日志、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        self._start_time = time.monotonic()
        config = get_config()
        setup_logging(config)

        server = SocketServer(config.host, config.port)
        # 传入绑定方法 self._ping_handler，而不是调用它；服务端收到请求后才执行。
        server.register("core.ping", self._ping_handler)

        # await 等待监听启动完成；已有连接的处理由事件循环继续调度。
        addr = await server.start()
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", config)

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
