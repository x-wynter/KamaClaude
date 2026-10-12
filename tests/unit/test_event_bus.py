# 事件总线测试：用局部异步回调收集事件，验证调用顺序与对象引用。

from __future__ import annotations

from pydantic import BaseModel

from kama_claude.core.events.bus import EventBus


class _FakeEvent(BaseModel):
    value: str


# 功能：发布事件后订阅函数能收到事件。
# 设计：闭包收集接收值，再比较列表；这里的 == 检查相等而非严格对象身份。
async def test_publish_reaches_subscriber() -> None:
    bus = EventBus()
    received: list[BaseModel] = []

    # 局部异步函数通过闭包访问 received；这里只定义，subscribe 后才被调用。
    async def handler(event: BaseModel) -> None:
        received.append(event)

    bus.subscribe(handler)
    event = _FakeEvent(value="hello")
    await bus.publish(event)
    assert received == [event]


# 功能：每个订阅者都被调用一次。
# 设计：用两个独立计数槽区分处理器，防止重复调用一个处理器也被误判成功。
async def test_multiple_subscribers_all_receive() -> None:
    bus = EventBus()
    counts = [0, 0]

    async def h1(e: BaseModel) -> None:
        # 修改外层列表元素无需 nonlocal；若重新绑定外层变量本身才涉及另一条规则。
        counts[0] += 1

    async def h2(e: BaseModel) -> None:
        counts[1] += 1

    bus.subscribe(h1)
    bus.subscribe(h2)
    await bus.publish(_FakeEvent(value="x"))
    assert counts == [1, 1]


# 功能：回调按注册顺序调用。
# 设计：每个回调追加不同数字，结果列表直接反映顺序。
async def test_subscribers_called_in_order() -> None:
    bus = EventBus()
    order: list[int] = []

    async def h1(e: BaseModel) -> None:
        order.append(1)

    async def h2(e: BaseModel) -> None:
        order.append(2)

    bus.subscribe(h1)
    bus.subscribe(h2)
    await bus.publish(_FakeEvent(value="x"))
    assert order == [1, 2]


# 功能：空总线发布事件不抛异常。
# 设计：不注册任何回调，覆盖空列表循环路径。
async def test_no_subscribers_publish_is_noop() -> None:
    bus = EventBus()
    await bus.publish(_FakeEvent(value="x"))  # should not raise
