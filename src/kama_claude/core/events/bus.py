# ═══ 文件职责：同一进程中的事件分发 ═══
# 协作关系：Loop、Runner、Provider 发布事件；文件记录器和 IPC 广播器是订阅者。
# 阅读路线：构造列表 → subscribe 保存回调 → publish 逐个等待回调。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

# Callable 描述参数，Awaitable[None] 表示可等待且完成后不返回业务值。
type EventHandler = Callable[[BaseModel], Awaitable[None]]


# ── 类：EventBus ──
# 职责：保存异步回调列表，按注册顺序分发进程内事件。
# 字段说明：
#   _subscribers：按注册顺序保存异步回调，publish 会逐个调用。
class EventBus:
    # ── 初始化：__init__ ──
    # 用途：为本总线创建独立订阅列表；不同 EventBus 实例互不共享。
    def __init__(self) -> None:
        self._subscribers: list[EventHandler] = []

    # ── 函数：subscribe ──
    # 用途：注册一个事件处理函数
    def subscribe(self, handler: EventHandler) -> None:
        # 保存函数本身，不写 handler()；实际调用发生在 publish。
        # 允许重复订阅，同一 handler 注册两次就会被调用两次。
        self._subscribers.append(handler)

    # ── 函数：publish ──
    # 用途：按注册顺序依次调用所有订阅者
    async def publish(self, event: BaseModel) -> None:
        # 顺序等待所有订阅者；没有自动并发或异常隔离，异常会传回发布者。
        for handler in self._subscribers:
            # 传递的是同一个模型对象，没有复制或 JSON 序列化。
            await handler(event)
