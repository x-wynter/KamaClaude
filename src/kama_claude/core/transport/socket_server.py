# ═══ 文件职责：Core 的 TCP 请求入口 ═══
# 协作关系：字节行 → JSON → 请求模型 → 业务 handler → 响应模型 → 字节行。
# 阅读路线：register/start → _handle_connection → _read_loop → _handle_line → _send。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from kama_claude.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    HandlerError,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)
from kama_claude.core.trace.record import TraceRecord
from kama_claude.core.trace.writer import TraceWriter
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster

logger = logging.getLogger(__name__)

# Python 3.12 的 type 语法定义类型别名。Callable 描述函数参数和返回值。
# 这里表示：接收一个字典，返回可以 await 的对象；最终结果类型不限。
type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# ContextVar 保存协程上下文中的 writer；不同连接 Task 不会像全局变量那样互相覆盖。
# 它让 handler(params) 接口保持不变，同时能向当前连接推送订阅事件。
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var")


# 返回 IPC trace 的 UTC 采集时间。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# 返回当前 handler 调用所属连接的 StreamWriter
# ── 函数：get_connection_writer ──
# 用途：返回当前 handler 调用所属连接的 StreamWriter
def get_connection_writer() -> asyncio.StreamWriter:
    return _writer_var.get()

_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果


# ── 类：SocketServer ──
# 职责：维护监听服务器和方法表，为每个连接处理请求。
# 字段说明：
#   _host / _port：监听地址；_handlers：方法名到业务函数的映射。
#   _server：监听服务器对象；_broadcaster：断开连接时用来移除订阅。
class SocketServer:
    # ── 初始化：__init__ ──
    # 用途：建立本实例的初始状态；字段含义见类上方的说明。
    def __init__(
        self,
        host: str,
        port: int,
        broadcaster: IpcEventBroadcaster | None = None,
        trace: TraceWriter | None = None,
        on_disconnect: Callable[[asyncio.StreamWriter], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        # 字典将方法名映射到处理函数；下划线表示内部属性，并不是强制私有。
        self._handlers: dict[str, CommandHandler] = {}
        # | None 表示尚未启动时可以为空，启动后保存 asyncio 的服务器对象。
        self._server: asyncio.AbstractServer | None = None
        self._broadcaster = broadcaster
        self._trace = trace
        self._on_disconnect = on_disconnect
        self._requests: set[asyncio.Task[None]] = set()
        self._active_writers: set[asyncio.StreamWriter] = set()

    # ── 函数：register ──
    # 用途：注册一个方法名对应的命令处理函数
    def register(self, method: str, handler: CommandHandler) -> None:
        # 函数也能作为值存入字典；再次注册同一个名称会覆盖之前的处理器。
        self._handlers[method] = handler

    # ── 函数：start ──
    # 用途：启动 TCP 服务器；若端口已被占用则退出进程
    async def start(self) -> str:
        # 步骤 1：探测地址是否已被其他服务占用。
        try:
            # 先尝试连接来探测地址是否已有服务；_r 表示取得但不使用的读流。
            # 探测成功只说明能连接，不会验证对方确实是 kama-core。
            _r, w = await asyncio.open_connection(self._host, self._port)
            w.close()
            await w.wait_closed()
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        # 探测失败时继续尝试监听；pass 是空操作，不会提前结束函数。
        except (ConnectionRefusedError, OSError):
            pass

        # 步骤 2：建立监听并设置连接回调。
        # start_server 创建监听服务器；每当客户端连接，调用下面传入的回调。
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            # 设置 StreamReader 的缓冲限制；它不是整个连接累计传输量的上限。
            limit=_MAX_LINE_BYTES,
        )

        # 步骤 3：返回用于日志展示的监听地址。
        return f"{self._host}:{self._port}"

    # ── 函数：stop ──
    # 用途：关闭服务器，最多等待 2 秒
    async def stop(self) -> None:
        if self._server is None:
            return
        # 学习分支保留请求取消/等待，再合入官方活跃 writer 关闭，审批请求不会遗留。
        pending = list(self._requests)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for writer in list(self._active_writers):
            writer.close()
        self._server.close()
        # wait_for 限制等待关闭的时长；超过 2 秒会抛出 TimeoutError。
        await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    # ── 函数：_handle_connection ──
    # 用途：处理单个客户端连接，完成后关闭写流
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 步骤 1：记录对端地址。
        # 读取连接对端地址，缺少该信息时使用默认文字；用于日志记录。
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.debug("client connected: %s", peer)

        # 步骤 2：处理此连接上的请求流。
        self._active_writers.add(writer)
        try:
            await self._read_loop(reader, writer)

        # 步骤 3：移除订阅并关闭此客户端写流。
        # 即使读取或处理请求时出错，也进入 finally，尝试关闭当前客户端写流。
        finally:
            if self._on_disconnect is not None:
                self._on_disconnect(writer)
            self._active_writers.discard(writer)
            if self._broadcaster is not None:
                self._broadcaster.unsubscribe(writer)
            try:
                writer.close()
            except Exception:
                pass
            logger.debug("client disconnected: %s", peer)

    # ── 函数：_read_loop ──
    # 用途：持续读取 JSON 行，各请求独立调度，允许审批与长请求交替推进
    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 持续读取同一个连接的多条请求，直到 EOF 或某个退出条件出现。
        while True:
            try:
                # await 等到一行或 EOF；TCP 数据可能分批到达，由 StreamReader 组织。
                line = await reader.readline()
            # 此分支意图处理超长行；但 Python 3.12 的 readline 通常将超限转成 ValueError，
            # 因此不能据此认为所有超长输入都能进入本分支。这里仅说明现状，不改逻辑。
            except asyncio.LimitOverrunError:
                await self._send(writer, make_error(None, INVALID_REQUEST, "Request too large"))
                return

            # EOF 时返回空字节串 b""，布尔值为 False；空白行 b"\n" 则不是 EOF。
            if not line:
                return

            # 每条命令独立作为 task 执行，避免长时间运行的 handler（如 session.send_message）
            # 阻塞读循环，使 permission.respond 等并发命令能被及时处理
            # create_task 立即返回任务引用；ContextVar 在子任务中隔离当前连接。
            task = asyncio.create_task(self._handle_line(line, writer))
            self._requests.add(task)
            task.add_done_callback(self._request_done)

    # 回收请求任务并读取异常，防止后台异常无人接收。
    def _request_done(self, task: asyncio.Task[None]) -> None:
        self._requests.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("request task failed: %s", task.exception())

    # ── 函数：_handle_line ──
    # 用途：解析单行 JSON-RPC 请求并调用对应 handler，将结果或错误写回客户端
    # 输入：line 是一行网络字节，writer 指向当前客户端；响应直接写回连接。
    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        # 步骤 1：解析 JSON 语法。
        try:
            # 第一层只做 JSON 语法解析；合法 JSON 也可能是列表等不符合协议的数据。
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        # 步骤 2：校验请求外壳的字段。
        try:
            # 第二层调用 Pydantic 校验协议字段，失败会抛出 ValidationError。
            req = JsonRpcRequest.model_validate(raw)
        # 这处捕获请求外壳的校验错误；后面的同类捕获则处理业务函数抛出的校验错误。
        except ValidationError as e:
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return

        if self._trace is not None:
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CLIENT→CORE",
                    layer="ipc",
                    kind="command",
                    client_id=client_id,
                    data={"method": req.method, "id": req.id, "params": req.params},
                )
            )

        # 步骤 3：按 method 找到业务处理函数。
        # 按方法名查字典；get 找不到时返回 None，下面回复方法不存在错误。
        handler = self._handlers.get(req.method)
        if handler is None:
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        # 步骤 4：绑定当前连接并调用业务函数，处理业务异常。
        # 在 await handler 前设置上下文，handler 内的 get_connection_writer 可取到它。
        _writer_var.set(writer)
        try:
            # 调用注册的业务函数并等待结果；参数是已解析的 params 字典。
            result = await handler(req.params)
        # S3 支持业务错误接口；code/data 由 handler 给出，不统一改写成 -32603。
        except HandlerError as e:
            await self._send(writer, make_error(req.id, e.code, str(e), e.data))
            return
        except ValidationError as e:
            await self._send(
                writer,
                # 当前实际使用 INVALID_REQUEST（-32600），并未使用 INVALID_PARAMS（-32602）。
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            # 记录错误消息及异常堆栈；响应中仅返回通用 Internal error。
            logger.exception("handler %s raised: %s", req.method, e)
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        # 步骤 5：将业务结果装进响应外壳并发送。
        # 条件表达式：是 Pydantic 模型就转为字典，否则保留原值。
        # model_dump 返回 Python 数据，model_dump_json 才返回 JSON 字符串。
        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            await self._send(writer, JsonRpcSuccess(id=req.id, result=result_data))
        except (ConnectionResetError, BrokenPipeError, OSError):
            logger.debug("client disconnected before response for %s", req.method)

    # ── 函数：_send ──
    # 用途：将 pydantic 消息序列化为 JSON 行并写入流，随后刷新缓冲区
    async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
        # 先序列化为 JSON 字符串，再编码为字节；b"\n" 是字节形式的消息分隔符。
        writer.write(msg.model_dump_json().encode() + b"\n")
        # 等待写缓冲压力缓解，避免持续写入过快；不是对端已处理消息的确认。
        await writer.drain()
        if self._trace is not None:
            # 响应成功写入缓冲后采集；顶层不填 run_id，请求 ID 在 data 内。
            kind = "error" if isinstance(msg, JsonRpcError) else "response"
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CORE→CLIENT",
                    layer="ipc",
                    kind=kind,
                    client_id=client_id,
                    data=msg.model_dump(),
                )
            )
