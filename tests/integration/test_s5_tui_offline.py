# S5 审批闭环：真实 Textual/TCP/Core，仅替换付费模型边界。
from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from kama_claude.tui.app import ChatTextArea, KamaTuiApp, PermissionSelect


# 等待可观察状态而非依赖固定延迟，超时提供明确失败。
async def _wait(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("key,allowed", [("y", True), ("n", False)])
# 功能：验证键盘审批穿过同一连接，在长 RPC 未返回时允许或拒绝真正写文件。
# 设计：Pilot 操作生产控件，文件和历史双重断言排除只有界面变化而未执行的假闭环。
async def test_s5_tui_permission_roundtrip(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path,
    key: str, allowed: bool,
) -> None:
    app = KamaTuiApp("127.0.0.1", free_port)
    async with app.run_test(size=(100, 35)) as pilot:
        await _wait(lambda: app._session_id is not None)
        sid = app._session_id
        app.query_one(ChatTextArea).text = "s5 permission 中文🧭"
        await pilot.press("enter")
        await _wait(lambda: len(app.query(PermissionSelect)) == 1)
        assert app._busy
        assert not (tmp_path / "s5-approved.txt").exists()
        await pilot.press(key)
        await _wait(lambda: not app._busy)
        await pilot.pause()
        assert not app._pending_permission_blocks
        assert len(app.query(PermissionSelect)) == 0
        output = tmp_path / "s5-approved.txt"
        assert output.exists() == allowed
        if allowed:
            assert output.read_text(encoding="utf-8") == "中文审批🧭"
        root = tmp_path / ".kama" / "sessions" / str(sid)
        rows = [json.loads(line) for line in (root / "thread.jsonl").read_text(encoding="utf-8").splitlines()]
        tool_result = rows[2]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert tool_result.get("is_error", False) == (not allowed)
        await pilot.press("ctrl+q")


# 功能：验证实际 socket 断开会拒绝当时挂起的审批，让模型运行继续收尾。
# 设计：独立接收协程观察真实请求事件后关连接，再从隔离会话文件确认拒绝与本轮结束。
async def test_s5_disconnect_releases_approval(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path,
) -> None:
    from kama_claude.core.transport.socket_client import SocketClient

    client = SocketClient("127.0.0.1", free_port)
    requested = asyncio.Event()

    # 只通知测试审批已进入等待，避免把本测试写成提前回应的单元测试。
    async def on_event(event: dict) -> None:
        if event.get("type") == "permission.requested":
            requested.set()

    await client.connect()
    client.on_event(on_event)
    receiver = asyncio.create_task(client.run_event_loop())
    sender = None
    try:
        await client.send_command("event.subscribe", {"topics": ["permission.*"], "scope": "global"})
        session = await client.send_command("session.create", {"mode": "chat"})
        sender = asyncio.create_task(client.send_command("session.send_message", {
            "session_id": session["session_id"], "content": "s5 permission disconnect",
        }))
        await asyncio.wait_for(requested.wait(), 10)
        await client.close()
        root = tmp_path / ".kama" / "sessions" / str(session["session_id"])

        # 完成时 meta 才进入 waiting_for_input；检查最终持久状态而非仅关闭成功。
        def finished() -> bool:
            return json.loads((root / "meta.json").read_text(encoding="utf-8"))["status"] == "waiting_for_input"

        await _wait(finished)
        assert not (tmp_path / "s5-approved.txt").exists()
        rows = [json.loads(line) for line in next((root / "runs").glob("*/events.jsonl")).read_text(encoding="utf-8").splitlines()]
        assert any(row["type"] == "permission.denied" for row in rows)
        assert any(row["type"] == "run.finished" for row in rows)
    finally:
        await client.close()
        receiver.cancel()
        if sender is not None:
            sender.cancel()
        await asyncio.gather(receiver, *([sender] if sender is not None else []), return_exceptions=True)
