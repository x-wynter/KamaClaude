from __future__ import annotations

import asyncio
import signal
from unittest.mock import Mock

import pytest

from kama_claude.core.app import _wait_for_shutdown


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("native", [True, False])
# 功能：验证两种信号注册方式都能触发退出并恢复原处理器
# 设计：模拟平台能力并直接调用注册的回调，避免真实信号中断测试进程
async def test_shutdown_signal(monkeypatch: pytest.MonkeyPatch, sig: int, native: bool) -> None:
    loop = asyncio.get_running_loop()
    add = Mock(side_effect=None if native else NotImplementedError)
    remove = Mock()
    previous = signal.getsignal(sig)
    install = Mock()
    monkeypatch.setattr(loop, "add_signal_handler", add)
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    monkeypatch.setattr(signal, "signal", install)

    task = asyncio.create_task(_wait_for_shutdown())
    try:
        await asyncio.sleep(0)
        assert not task.done()
        calls = add.call_args_list if native else install.call_args_list
        callback = next(call.args[1] for call in calls if call.args[0] == sig)
        if native:
            callback()
        else:
            callback(sig, None)
        await asyncio.wait_for(task, timeout=1)
        install.assert_any_call(sig, previous)
        if native:
            remove.assert_any_call(sig)
        else:
            remove.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# 功能：验证等待退出的任务取消后会恢复同步信号处理器
# 设计：在真实事件循环上取消任务，检查异常路径不会遗留进程级处理器
async def test_shutdown_cancel_restores_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", Mock(side_effect=NotImplementedError))
    install = Mock()
    monkeypatch.setattr(signal, "signal", install)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    task = asyncio.create_task(_wait_for_shutdown())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for sig, handler in previous.items():
        install.assert_any_call(sig, handler)
