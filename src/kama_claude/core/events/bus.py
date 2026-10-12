# 进程内事件总线：订阅者是异步函数；它与 TCP JSON-RPC 传输不是同一层。

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

# Callable 描述参数，Awaitable[None] 表示可等待且完成后不返回业务值。
type EventHandler = Callable[[BaseModel], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[EventHandler] = []

    # 注册一个事件处理函数
    def subscribe(self, handler: EventHandler) -> None:
        self._subscribers.append(handler)

    # 按注册顺序依次调用所有订阅者
    async def publish(self, event: BaseModel) -> None:
        # 顺序等待所有订阅者；没有自动并发或异常隔离，异常会传回发布者。
        for handler in self._subscribers:
            # 传递的是同一个模型对象，没有复制或 JSON 序列化。
            await handler(event)
