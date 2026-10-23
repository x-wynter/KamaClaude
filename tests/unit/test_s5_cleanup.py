# 审批等待的资源生命周期；不调用真实模型、不读取用户配置。
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from kama_claude.core.permissions.manager import PermissionManager


@pytest.mark.parametrize("mode", ["cancel", "emitter_error", "timeout", "disconnect"])
# 功能：验证取消、广播异常、超时和断连都不会遗留 pending Future。
# 设计：直接等待真实 Manager；只替换广播出口，分别断言异常传播和拒绝结果。
async def test_pending_cleanup(mode: str) -> None:
    manager = PermissionManager(timeout_s=0.01 if mode == "timeout" else 0)
    emitted = asyncio.Event()

    # 事件已登记后通知测试，模拟客户端或总线失败。
    async def emit(event: dict) -> None:
        emitted.set()
        if mode == "emitter_error":
            raise RuntimeError("broadcast failed")

    task = asyncio.create_task(manager.check_and_wait("id", "bash", {}, "session", emit))
    await emitted.wait()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif mode == "emitter_error":
        with pytest.raises(RuntimeError, match="broadcast failed"):
            await task
    else:
        if mode == "disconnect":
            manager.cancel_session("session")
        assert (await task)[0] is False
    assert not manager._pending


# 功能：验证外层任务取消会杀死并等待 shell 直接子进程。
# 设计：模拟尚未结束的进程，检查 kill 与 communicate，不启动真实长寿命进程。
async def test_bash_cancel_reaps_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from kama_claude.core.tools.builtin.bash import BashTool

    entered = asyncio.Event()
    process = Mock(returncode=None)

    # 第一次等待被取消，第二次返回以模拟进程已回收。
    async def communicate() -> tuple[bytes, None]:
        if not entered.is_set():
            entered.set()
            await asyncio.Event().wait()
        return b"", None

    process.communicate = AsyncMock(side_effect=communicate)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", AsyncMock(return_value=process))
    task = asyncio.create_task(BashTool().invoke({"command": "echo unused"}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2
