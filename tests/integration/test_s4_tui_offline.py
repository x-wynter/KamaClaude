# S4 本地链路：Textual Pilot → 真实 socket/Core/SessionManager/Runner → 假模型。
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from kama_claude.tui.app import ChatTextArea, KamaTuiApp


# 等待真实界面状态，使用截止时间避免固定 sleep 掩盖异步时序错误。
async def _ready(app: KamaTuiApp) -> None:
    async with asyncio.timeout(10):
        while app._session_id is None or app._busy or app.query_one(ChatTextArea).disabled:
            await asyncio.sleep(0.05)


# 功能：验证挂载后的 TUI 支持换行、提交、恢复输入、多轮历史和关闭会话。
# 设计：Pilot 发真实键盘事件，跨真实 TCP 使用离线模型，检查 UTF-8 文件而非仅 mock widget。
async def test_tui_chat_keyboard_and_persistence(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path,
) -> None:
    app = KamaTuiApp("127.0.0.1", free_port)
    async with app.run_test(size=(100, 35)) as pilot:
        await _ready(app)
        sid = app._session_id
        prompt = app.query_one(ChatTextArea)
        prompt.text = "中文学习🧭"
        prompt.move_cursor((0, len(prompt.text)))
        await pilot.press("ctrl+j")
        assert prompt.text == "中文学习🧭\n"
        await pilot.press("enter")
        await _ready(app)
        await pilot.pause()
        assert prompt.text == ""
        prompt.text = "第二轮"
        await pilot.press("enter")
        await _ready(app)
        await pilot.pause()
        root = tmp_path / ".kama" / "sessions" / str(sid)
        rows = [json.loads(line) for line in (root / "thread.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
        assert rows[0]["content"] == "中文学习🧭"
        assert rows[2]["content"] == "第二轮"
        assert len(list((root / "runs").glob("*/events.jsonl"))) == 2
        await pilot.press("ctrl+q")
    assert json.loads((root / "meta.json").read_text(encoding="utf-8"))["status"] == "closed"
