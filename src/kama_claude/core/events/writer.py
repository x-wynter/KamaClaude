# 事件文件订阅者：把模型逐行写入 events.jsonl；每一行是独立 JSON。

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from kama_claude.core.events.bus import EventBus

logger = logging.getLogger(__name__)


class EventWriter:
    # 保存目标路径和空文件句柄；构造对象时尚未打开文件。
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    # 打开事件文件（追加模式），供 async with 使用
    # async with 自动 await __aenter__，返回值绑定到 as writer。
    async def __aenter__(self) -> EventWriter:
        # parent 取文件的父目录；mkdir 的两个参数允许一次建立多层缺失目录。
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # a 是追加模式，已有内容保留；encoding 指定磁盘文本编码。
        self._file = open(self._path, "a", encoding="utf-8")
        # 把当前对象交给 async with 后面的 as writer，因此 writer.handle 可以被调用。
        return self

    # 关闭事件文件
    # *args 接收异常类型、异常对象和 traceback；返回 None 不会吞掉异常。
    async def __aexit__(self, *args: object) -> None:
        # 退出上下文时释放文件句柄；随后置 None，表示已经不处于打开状态。
        if self._file is not None:
            self._file.close()
            self._file = None

    # 将事件序列化为 JSON 行并写入文件，写入失败时记录日志但不抛出异常
    # 虽然接口为 async，内部文件 write/flush 仍是同步调用。
    async def handle(self, event: BaseModel) -> None:
        # 未进入上下文或已经退出时直接返回；不会隐式替调用方打开文件。
        if self._file is None:
            return
        try:
            # model_dump_json 返回字符串；追加换行后就是一条 JSONL 记录。
            self._file.write(event.model_dump_json() + "\n")
            # flush 推进 Python 写缓冲；并非 fsync，也不保证数据已落到物理磁盘。
            self._file.flush()
        # 元组表示捕获两类异常：操作系统 I/O 错误，或写入已关闭文件等值错误。
        # 这里记录日志后继续，让日志写入失败不直接终止正常业务调用。
        except (OSError, ValueError) as e:
            logger.error("EventWriter: failed to write event: %s", e)

    # 将 handle 注册为 bus 的订阅者
    def subscribe(self, bus: EventBus) -> None:
        # self.handle 是已绑定到当前实例的方法；总线调用时无需再次传入 self。
        bus.subscribe(self.handle)
