# 职责：保存本轮后台 asyncio.Task 及其 ExecutionContext，供查询与收尾。
# 协作：Runner 创建；根和嵌套 SpawnAgentTool 共享；AgentResultTool 按 ID 查询。
# 推荐阅读：register → get → cancel_all。不是持久化任务队列，也没有跨 run 恢复。

from __future__ import annotations

import asyncio

from kama_claude.core.context import ExecutionContext


# 管理后台 subagent 任务的生命周期：注册、查询、批量取消
class BackgroundTaskRegistry:
    # 创建内存索引；没有磁盘恢复，也不自行启动任务。
    def __init__(self) -> None:
        self._tasks: dict[str, tuple[asyncio.Task[None], ExecutionContext]] = {}

    # 注册一个后台任务及其执行上下文
    def register(
        self,
        run_id: str,
        task: asyncio.Task[None],
        context: ExecutionContext,
    ) -> None:
        self._tasks[run_id] = (task, context)

    # 查询后台任务及其上下文；不存在时返回 None
    def get(self, run_id: str) -> tuple[asyncio.Task[None], ExecutionContext] | None:
        return self._tasks.get(run_id)

    # Runner 收尾调用；循环快照以收拢取消期间可能登记的嵌套任务。
    # 取消并等待全部后台任务，读取异常后清空本轮注册表
    async def cancel_all(self) -> None:
        while self._tasks:
            entries = self.all()
            self._tasks.clear()
            for task, context in entries:
                if not task.done():
                    context.mark_failed("cancelled")
                    task.cancel()
            await asyncio.gather(*(task for task, _ in entries), return_exceptions=True)

    # 返回已登记对象快照，供收尾和测试检查
    def all(self) -> list[tuple[asyncio.Task[None], ExecutionContext]]:
        return list(self._tasks.values())
