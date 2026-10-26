# ═══ 文件职责：CLI 与 TUI 共用的 TCP 客户端 ═══
# 协作关系：调用方发送命令；接收循环将响应交给 Future，将事件交给回调。
# 阅读路线：connect → on_event → 启动 run_event_loop → send_command → close。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from kama_claude.core.bus.envelope import JsonRpcRequest

# Callable 的参数是事件字典；Awaitable[None] 表示回调必须可以 await。
# 跨进程传输后得到普通 dict，不再是服务端内存中的 Pydantic 实例。
type EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果


# ── 类：IpcError ──
# 职责：将服务端错误码和消息包装为客户端可以捕获的异常。
# 字段说明：
#   code：服务端错误码；错误文字交给 RuntimeError 保存。
class IpcError(RuntimeError):
    # ── 初始化：__init__ ──
    # 用途：建立本实例的初始状态；字段含义见类上方的说明。
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


# ── 类：SocketClient ──
# 职责：维护一个 TCP 连接，并区分命令响应和主动事件。
# 字段说明：
#   _host / _port：服务地址；_reader / _writer：连接建立后得到的读流/写流。
#   _pending：请求 ID 到 Future 的映射；_event_handlers：收到事件后调用的函数列表。
class SocketClient:
    # ── 初始化：__init__ ──
    # 用途：建立本实例的初始状态；字段含义见类上方的说明。
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        # 请求 ID → Future：Future 像一个暂时空着的结果盒，由接收循环填入结果。
        # 多条请求即使交错返回，也可以根据 ID 唤醒各自的等待者。
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: list[EventHandler] = []

    # ── 函数：connect ──
    # 用途：建立到 core 守护进程的 TCP 连接
    async def connect(self) -> None:
        # 库函数：asyncio.open_connection 建立 TCP 连接，返回按字节读取/写入的两个流对象。
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, limit=_MAX_LINE_BYTES
        )

    # ── 函数：close ──
    # 用途：关闭 TCP 连接并等待底层 socket 释放
    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                # 库函数：wait_closed 等待底层连接关闭；wait_for 将等待限制为 1 秒。
                # 超时会抛出 TimeoutError，下面捕获后结束清理。
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass

    # ── 函数：on_event ──
    # 用途：注册服务器推送事件的回调，可多次调用以添加多个 handler
    def on_event(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    # ── 函数：send_command ──
    # 用途：发送 JSON-RPC 命令并等待响应，成功返回 result dict，失败抛出 IpcError
    # 输入：method 是命令名，params 是参数字典；调用方须先启动 run_event_loop。
    # 输出：返回该命令的 result；agent.run 的 result 只表示受理，不表示任务完成。
    async def send_command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        # 步骤 1：确认连接已经建立。
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")

        # 步骤 2：创建请求 ID、请求模型和等待结果的 Future。
        # UUID 为本次命令生成关联 ID；它与任务 run_id、订阅 sub_id 用途不同。
        req_id = str(uuid.uuid4())
        request = JsonRpcRequest(id=req_id, method=method, params=params)
        # 库函数：get_running_loop 取得当前事件循环；create_future 创建尚无结果的等待对象。
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

        # 步骤 3：先登记等待者，再编码并发送请求。
        # 先登记再发送，避免响应到达时找不到对应的等待者。
        self._pending[req_id] = fut
        # 库函数：model_dump_json 转成 JSON 文本，encode 转成 UTF-8 字节；换行标记消息边界。
        self._writer.write(request.model_dump_json().encode() + b"\n")
        # 库函数：drain 在写缓冲过满时等待压力缓解，不表示服务端已经处理完命令。
        await self._writer.drain()

        # 步骤 4：等待接收循环按 ID 填回结果或异常。
        # 本方法不读 socket；必须另有 run_event_loop 任务读取并 set_result。
        # await 让出执行权，不是阻塞整个进程。当前此处没有内置请求超时。
        return await fut

    # ── 函数：run_event_loop ──
    # 用途：持续读取服务器消息，分发 RPC 响应到 pending future 或事件到 event handler
    async def run_event_loop(self) -> None:
        # 步骤 1：确认存在读流。
        if self._reader is None:
            raise RuntimeError("not connected — call connect() first")

        # 步骤 2：持续按行读取消息，交给分发函数。
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    # 单行超出 limit；丢弃本行，继续读取后续消息
                    continue
                # readline 按换行分帧；b"" 表示对端关闭，而非一条空事件。
                if not line:
                    break
                await self._dispatch(line)

        # 步骤 3：连接结束时取消并清空未完成的请求。
        finally:
            # 连接退出时取消未完成请求，避免请求者永远等待不存在的响应。
            # done() 同时覆盖成功、异常和取消状态，不能再次完成已结束的 Future。
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    # ── 函数：_dispatch ──
    # 用途：解析单行消息并路由到 pending future（RPC 响应）或 event handler（服务器推送）
    async def _dispatch(self, line: bytes) -> None:
        # 步骤 1：解析 JSON；格式错误时忽略本行。
        try:
            # 库函数：json.loads 将 JSON 字节解析成 Python 数据；此处按协议预期使用字典。
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return

        # 步骤 2：响应分支：找到原请求的 Future，填入结果或异常。
        # 同一读循环按外壳字段分流：jsonrpc 是命令响应，kind=event 是事件推送。
        # 回放事件可能先于订阅响应到达，所以不能把下一行直接当成本次响应。
        if "jsonrpc" in msg:
            req_id: str | None = msg.get("id")
            if req_id and req_id in self._pending:
                # pop 同时取出并删除条目，这个请求已经不再处于等待状态。
                fut = self._pending.pop(req_id)
                if not fut.done():
                    if "error" in msg:
                        err = msg["error"]
                        # 在 Future 中存异常；异常随后从 send_command 的 await fut 抛出。
                        fut.set_exception(
                            IpcError(err.get("code", -1), err.get("message", "unknown"))
                        )
                    else:
                        fut.set_result(msg.get("result") or {})

        # 步骤 3：事件分支：依次调用已注册的事件处理函数。
        elif msg.get("kind") == "event":
            event_data: dict[str, Any] = msg.get("event", {})
            # 顺序等待回调，不是并行广播；慢回调会延迟后续 socket 消息的处理。
            for handler in self._event_handlers:
                await handler(event_data)
