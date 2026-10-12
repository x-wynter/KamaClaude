# 退出信号测试：用 Mock 替换信号注册接口，直接调用回调，不向进程发送真实信号。
# monkeypatch 是 pytest 夹具，测试结束后自动恢复被替换的属性。

from __future__ import annotations

import asyncio
import signal
from unittest.mock import Mock

import pytest

from kama_claude.core.app import _wait_for_shutdown


# 两层 parametrize 做笛卡尔组合：2 种信号 × 2 种平台能力，共运行 4 组。
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("native", [True, False])
# 功能：验证两种信号注册方式都能触发退出并恢复原处理器
# 设计：模拟平台能力并直接调用注册的回调，避免真实信号中断测试进程
async def test_shutdown_signal(monkeypatch: pytest.MonkeyPatch, sig: int, native: bool) -> None:
    loop = asyncio.get_running_loop()
    # Mock 记录调用；side_effect=NotImplementedError 模拟平台不支持原生接口。
    add = Mock(side_effect=None if native else NotImplementedError)
    remove = Mock()
    previous = signal.getsignal(sig)
    install = Mock()
    monkeypatch.setattr(loop, "add_signal_handler", add)
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    monkeypatch.setattr(signal, "signal", install)

    # 把协程安排为任务，让测试可以在它等待信号期间检查并触发回调。
    task = asyncio.create_task(_wait_for_shutdown())
    try:
        # 让出一次执行机会，使新任务有机会完成信号注册并停在 wait。
        await asyncio.sleep(0)
        assert not task.done()
        # call_args_list 保存所有调用；按模拟的平台能力选择要检查的注册接口。
        calls = add.call_args_list if native else install.call_args_list
        # 生成器筛选指定信号的调用；next 取首个匹配项，args[1] 是注册的回调。
        callback = next(call.args[1] for call in calls if call.args[0] == sig)
        if native:
            callback()
        else:
            callback(sig, None)
        await asyncio.wait_for(task, timeout=1)
        # Mock 的断言检查是否曾使用指定参数调用，以确认恢复了原信号处理器。
        install.assert_any_call(sig, previous)
        if native:
            remove.assert_any_call(sig)
        else:
            remove.assert_not_called()
    finally:
        # 请求取消任务；取消异常会在协程可响应取消的位置抛出，并执行其中的清理。
        task.cancel()
        # 等待任务结束；return_exceptions=True 将异常作为结果收集，便于清理。
        await asyncio.gather(task, return_exceptions=True)


# 功能：验证等待退出的任务取消后会恢复同步信号处理器
# 设计：在真实事件循环上取消任务，检查异常路径不会遗留进程级处理器
async def test_shutdown_cancel_restores_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", Mock(side_effect=NotImplementedError))
    install = Mock()
    monkeypatch.setattr(signal, "signal", install)
    # 字典推导式：为两种信号分别保存旧处理器，便于逐一检查恢复结果。
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    task = asyncio.create_task(_wait_for_shutdown())
    await asyncio.sleep(0)
    task.cancel()
    # 期望代码块抛出指定异常；未抛异常或异常类型不匹配都会使测试失败。
    with pytest.raises(asyncio.CancelledError):
        await task
    for sig, handler in previous.items():
        install.assert_any_call(sig, handler)
