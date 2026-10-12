# 将进程内 EventBus 的事件转换为 TCP 消息；不负责运行任务或保存历史文件。

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from kama_claude.core.bus.envelope import EventPushEnvelope

logger = logging.getLogger(__name__)


# dataclass 自动生成初始化方法；每个对象保存一个客户端的一次订阅。
# 同一个 writer 可以有多个订阅，因此 unsubscribe 要移除所有对应项。
@dataclass
class _Subscription:
    sub_id: str
    writer: asyncio.StreamWriter
    topics: list[str]
    scope: str


class IpcEventBroadcaster:
    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    # 注册一个客户端订阅，返回 subscription_id
    def subscribe(
        self,
        writer: asyncio.StreamWriter,
        topics: list[str],
        scope: str = "global",
    ) -> str:
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        sub = _Subscription(sub_id=sub_id, writer=writer, topics=topics, scope=scope)
        self._subscriptions.append(sub)
        return sub_id

    # 移除指定 writer 的所有订阅
    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        # is 比较对象身份：必须是同一个连接的写流，而不是值碰巧相等。
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]

    # 将事件推送到所有匹配的订阅客户端，写入失败时延迟清理死连接
    async def handle(self, event: BaseModel) -> None:
        event_dict = event.model_dump()
        event_type: str = event_dict.get("type", "")
        run_id: str | None = event_dict.get("run_id")

        dead: list[asyncio.StreamWriter] = []

        # list 创建浅拷贝快照。遍历期间不直接删除原列表元素，避免遗漏后续项。
        for sub in list(self._subscriptions):
            if not self._matches_topic(event_type, sub.topics):
                continue
            # topic 和 scope 都匹配才发送；continue 表示跳过当前订阅。
            if not self._matches_scope(run_id, sub.scope):
                continue
            try:
                envelope = EventPushEnvelope(event=event_dict)
                # 模型 → JSON 文本 → UTF-8 字节 → 换行；每个换行结束一条消息。
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                # drain 等待写缓冲压力缓解，不是客户端已经显示完成的确认。
                # S2 逐个等待订阅者，慢连接可能拖慢事件发布。
                await sub.writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                logger.debug("dead connection for sub %s, scheduling cleanup", sub.sub_id)
                dead.append(sub.writer)

        # 统一清理已断开的连接；一个坏连接不应阻止后续事件持续发往其他客户端。
        for writer in dead:
            self.unsubscribe(writer)

    # 检查事件类型是否匹配订阅的 topic 列表（支持 fnmatch glob 模式）
    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool:
        # any 遇到首个匹配就返回 True；fnmatch 的 * 匹配任意字符，如 run.*。
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    # 检查事件 run_id 是否匹配订阅的 scope（global 全通，run:<id> 精确匹配）
    @staticmethod
    def _matches_scope(run_id: str | None, scope: str) -> bool:
        if scope == "global":
            return True
        if scope.startswith("run:"):
            # 切片 [4:] 去掉前缀 run:，留下要比较的完整任务 ID。
            return run_id == scope[4:]
        return False
