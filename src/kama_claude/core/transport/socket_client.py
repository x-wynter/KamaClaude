# S2 客户端：一个 TCP 连接同时承载命令响应和服务器主动推送的事件。
# 阅读顺序：connect → on_event → 启动 run_event_loop → send_command → close。

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

_MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB per frame


class IpcError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class SocketClient:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        # 请求 ID → Future：Future 像一个暂时空着的结果盒，由接收循环填入结果。
        # 多条请求即使交错返回，也可以根据 ID 唤醒各自的等待者。
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: list[EventHandler] = []

    # 建立到 core 守护进程的 TCP 连接
    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, limit=_MAX_LINE_BYTES
        )

    # 关闭 TCP 连接并等待底层 socket 释放
    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass

    # 注册服务器推送事件的回调，可多次调用以添加多个 handler
    def on_event(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    # 发送 JSON-RPC 命令并等待响应，成功返回 result dict，失败抛出 IpcError
    async def send_command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")
        # UUID 为本次命令生成关联 ID；它与任务 run_id、订阅 sub_id 用途不同。
        req_id = str(uuid.uuid4())
        request = JsonRpcRequest(id=req_id, method=method, params=params)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        # 先登记再发送，避免响应到达时找不到对应的等待者。
        self._pending[req_id] = fut
        self._writer.write(request.model_dump_json().encode() + b"\n")
        await self._writer.drain()
        # 本方法不读 socket；必须另有 run_event_loop 任务读取并 set_result。
        # await 让出执行权，不是阻塞整个进程。S2 此处没有内置请求超时。
        return await fut

    # 持续读取服务器消息，分发 RPC 响应到 pending future 或事件到 event handler
    async def run_event_loop(self) -> None:
        if self._reader is None:
            raise RuntimeError("not connected — call connect() first")
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    break
                # readline 按换行分帧；b"" 表示对端关闭，而非一条空事件。
                if not line:
                    break
                await self._dispatch(line)
        finally:
            # 连接退出时取消未完成请求，避免请求者永远等待不存在的响应。
            # done() 同时覆盖成功、异常和取消状态，不能再次完成已结束的 Future。
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    # 解析单行消息并路由到 pending future（RPC 响应）或 event handler（服务器推送）
    async def _dispatch(self, line: bytes) -> None:
        try:
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return

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
        elif msg.get("kind") == "event":
            event_data: dict[str, Any] = msg.get("event", {})
            # 顺序等待回调，不是并行广播；慢回调会延迟后续 socket 消息的处理。
            for handler in self._event_handlers:
                await handler(event_data)
