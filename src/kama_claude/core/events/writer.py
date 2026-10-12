# 事件文件订阅者：把模型逐行写入 events.jsonl；每一行是独立 JSON。

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from kama_claude.core.events.bus import EventBus

logger = logging.getLogger(__name__)


class EventWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    # 打开事件文件（追加模式），供 async with 使用
    # async with 自动 await __aenter__，返回值绑定到 as writer。
    async def __aenter__(self) -> EventWriter:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        return self

    # 关闭事件文件
    # *args 接收异常类型、异常对象和 traceback；返回 None 不会吞掉异常。
    async def __aexit__(self, *args: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    # 将事件序列化为 JSON 行并写入文件，写入失败时记录日志但不抛出异常
    # 虽然接口为 async，内部文件 write/flush 仍是同步调用。
    async def handle(self, event: BaseModel) -> None:
        if self._file is None:
            return
        try:
            self._file.write(event.model_dump_json() + "\n")
            # flush 推进 Python 写缓冲；并非 fsync，也不保证数据已落到物理磁盘。
            self._file.flush()
        except (OSError, ValueError) as e:
            logger.error("EventWriter: failed to write event: %s", e)

    # 将 handle 注册为 bus 的订阅者
    def subscribe(self, bus: EventBus) -> None:
        bus.subscribe(self.handle)
