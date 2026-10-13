# ═══ 文件职责：从进程内事件到客户端推送 ═══
# 协作关系：EventBus 调用 handle；广播器按订阅条件向各连接写入事件外壳。
# 阅读路线：先看订阅记录，再看注册/移除，最后读 handle 和两个匹配规则。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from kama_claude.core.bus.envelope import EventPushEnvelope

logger = logging.getLogger(__name__)


# ── 类：_Subscription ──
# 职责：一条订阅记录；同一个连接可以拥有多条记录。
# dataclass 自动生成初始化方法；每个对象保存一个客户端的一次订阅。
# 同一个 writer 可以有多个订阅，因此 unsubscribe 要移除所有对应项。
@dataclass
class _Subscription:
    # 字段：订阅编号。
    sub_id: str
    # 字段：向此客户端发送数据的 TCP 写流。
    writer: asyncio.StreamWriter
    # 字段：允许的事件名模式，例如 run.*。
    topics: list[str]
    # 字段：允许的任务范围：global 或 run:<任务 ID>。
    scope: str


# ── 类：IpcEventBroadcaster ──
# 职责：保存客户端订阅，并把匹配的事件发送到对应连接。
# 字段说明：
#   _subscriptions：所有连接的订阅记录；每条记录包含连接、主题和范围。
class IpcEventBroadcaster:
    # ── 初始化：__init__ ──
    # 用途：建立本实例的初始状态；字段含义见类上方的说明。
    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    # ── 函数：subscribe ──
    # 用途：注册一个客户端订阅，返回 subscription_id
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

    # ── 函数：unsubscribe ──
    # 用途：移除指定 writer 的所有订阅
    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        # is 比较对象身份：必须是同一个连接的写流，而不是值碰巧相等。
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]

    # ── 函数：handle ──
    # 用途：将事件推送到所有匹配的订阅客户端，写入失败时延迟清理死连接
    async def handle(self, event: BaseModel) -> None:
        # 步骤 1：将事件模型转换为字典，提取过滤字段。
        event_dict = event.model_dump()
        event_type: str = event_dict.get("type", "")
        run_id: str | None = event_dict.get("run_id")

        # 步骤 2：准备失效连接列表并遍历订阅快照。
        dead: list[asyncio.StreamWriter] = []

        # list 创建浅拷贝快照。遍历期间不直接删除原列表元素，避免遗漏后续项。
        for sub in list(self._subscriptions):
            # 步骤 3：检查事件主题和任务范围。
            if not self._matches_topic(event_type, sub.topics):
                continue
            # topic 和 scope 都匹配才发送；continue 表示跳过当前订阅。
            if not self._matches_scope(run_id, sub.scope):
                continue

            # 步骤 4：向匹配连接发送 JSON 行，记录发送失败的连接。
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

        # 步骤 5：遍历结束后统一移除失效连接。
        # 统一清理已断开的连接；一个坏连接不应阻止后续事件持续发往其他客户端。
        for writer in dead:
            self.unsubscribe(writer)

    # ── 函数：_matches_topic ──
    # 用途：检查事件类型是否匹配订阅的 topic 列表（支持 fnmatch glob 模式）
    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool:
        # any 遇到首个匹配就返回 True；fnmatch 的 * 匹配任意字符，如 run.*。
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    # ── 函数：_matches_scope ──
    # 用途：检查事件 run_id 是否匹配订阅的 scope（global 全通，run:<id> 精确匹配）
    @staticmethod
    def _matches_scope(run_id: str | None, scope: str) -> bool:
        if scope == "global":
            return True
        if scope.startswith("run:"):
            # 切片 [4:] 去掉前缀 run:，留下要比较的完整任务 ID。
            return run_id == scope[4:]
        return False
