# 退出信号测试：用 Mock 替换信号注册接口，直接调用回调，不向进程发送真实信号。
# monkeypatch 是 pytest 夹具，测试结束后自动恢复被替换的属性。

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
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

# 功能：S3 正常退出或等待异常时，都取消全部 run，等收尾后再停服务器和 trace。
# 设计：运行真实 CoreApp 生命周期，仅替换外部资源与长任务，检查清理顺序及集合移除。
@pytest.mark.parametrize("wait_fails", [False, True])
@pytest.mark.parametrize("chat", [False, True])
async def test_s3_shutdown_cleans_all_runs(
    monkeypatch: pytest.MonkeyPatch, wait_fails: bool, tmp_path: Path, chat: bool,
) -> None:
    from unittest.mock import AsyncMock

    from kama_claude.core import app as app_module
    from kama_claude.core.config import KamaConfig

    order: list[str] = []
    both_started = asyncio.Event()
    started = 0

    class WaitingRunner:
        # 接受 Core 传入的依赖，只保留本测试需要的可取消行为。
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        # 等两个 run 都启动；取消时模拟 Runner 的结束事件/文件收尾。
        async def run_and_capture(self, goal: str, **kwargs: object) -> None:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append(goal)

    server = Mock()
    server.start = AsyncMock(return_value="test-address")
    server.stop = AsyncMock(side_effect=lambda: order.append("server"))
    trace = Mock()
    trace.start = AsyncMock()
    trace.stop = AsyncMock(side_effect=lambda: order.append("trace"))
    monkeypatch.setattr(app_module, "get_config", lambda: KamaConfig())
    monkeypatch.setattr(app_module, "setup_logging", lambda config: None)
    monkeypatch.setattr(app_module, "SocketServer", Mock(return_value=server))
    monkeypatch.setattr(app_module, "TraceWriter", Mock(return_value=trace))
    monkeypatch.setattr(app_module, "AgentRunner", WaitingRunner)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    application = app_module.CoreApp()
    requests: list[asyncio.Task[object]] = []

    # 在服务接线后提交两个 run；模拟通知返回或等待阶段抛异常。
    async def wait_for_exit() -> None:
        await application._agent_run_handler({"goal": "first"})
        if chat:
            created = await application._session_create_handler({"mode": "chat"})
            requests.append(asyncio.create_task(application._session_send_handler({
                "session_id": created.session_id, "content": "second",
            })))
        else:
            await application._agent_run_handler({"goal": "second"})
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert len(application._running_runs) == 2
        if wait_fails:
            raise RuntimeError("test wait failure")

    monkeypatch.setattr(app_module, "_wait_for_shutdown", wait_for_exit)
    if wait_fails:
        with pytest.raises(RuntimeError, match="test wait failure"):
            await application.run()
    else:
        await application.run()
    await asyncio.gather(*requests, return_exceptions=True)
    assert set(order[:2]) == {"first", "second"}
    assert order[2:] == ["server", "trace"]
    assert not application._running_runs
    server.stop.assert_awaited_once()
    trace.stop.assert_awaited_once()
