# ═══ 文件职责：把统一 trace 队列写到 NDJSON 文件 ═══
# 协作关系：采集点 emit → Queue → _drain；CoreApp 在关闭前 stop 等待已入队记录。
# 阅读路线：先看 start/emit/stop，再读 _drain 的消费循环。
# 与 EventWriter 不同：一个 daemon 级队列跨多个 run，记录包含 IPC/event/LLM 三层。

from __future__ import annotations

import asyncio
from pathlib import Path

from kama_claude.core.trace.record import TraceRecord


# ── 类：TraceWriter ──
# 字段：_path 是 daemon 级文件；_queue 暂存记录；_task 是唯一消费协程。
# start/stop 由 CoreApp 管理，emit 被 IPC、事件订阅者和模型包装器调用。
class TraceWriter:
    # 初始化 TraceWriter；写入目标文件路径在 start() 前不会创建
    def __init__(self, path: Path) -> None:
        self._path = path
        # 标准库 asyncio.Queue 默认无大小限制；生产和消费都在同一事件循环。
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    # 创建目录、启动后台 drain task
    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # create_task 调度 _drain 协程，不新建线程/进程；start 返回不代表已经写过文件。
        self._task = asyncio.create_task(self._drain())

    # 等待队列清空后取消 drain task
    async def stop(self) -> None:
        # 步骤 1：join 等待每次 put 都对应 task_done，避免先取消造成队列尾部丢失。
        await self._queue.join()
        # 步骤 2：取消正在等下一条记录的消费者，再 await 回收它。
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # 非阻塞地将 record 放入写入队列
    def emit(self, record: TraceRecord) -> None:
        # put_nowait 立即排队；这里还未写文件，也没有复制 data 中的嵌套对象。
        self._queue.put_nowait(record)

    # 持续从队列读取 record 并追加写入文件
    async def _drain(self) -> None:
        # 步骤 1：内置 open 以 UTF-8 追加打开文件，退出 with 自动关闭。
        with open(self._path, "a", encoding="utf-8") as f:
            while True:
                # 步骤 2：队列空时 await 让出执行权；有记录后再序列化成一行。
                record = await self._queue.get()
                try:
                    f.write(record.model_dump_json() + "\n")
                    # flush 刷新 Python 缓冲，并不是磁盘 fsync；write/flush 仍同步执行。
                    f.flush()
                # 步骤 3：即使本条写入失败也完成队列计数；没有自动重启失败消费者的机制。
                finally:
                    self._queue.task_done()
