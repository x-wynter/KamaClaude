# S6 手动压缩贯穿真实 TUI、TCP、Core、Manager 和 UTF-8 thread 文件。
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kama_claude.tui.app import ChatTextArea, KamaTuiApp
from tests.integration.test_s5_tui_offline import _wait


# 功能：键盘 /compact 实际改写历史，随后输入仍能完成一次会话执行。
# 设计：沿用隔离 daemon，仅替换 Provider；检查备份、摘要和后续消息，排除界面假成功。
async def test_tui_compact_and_continue(running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path) -> None:
    app = KamaTuiApp("127.0.0.1", free_port)
    async with app.run_test(size=(100, 35)) as pilot:
        await _wait(lambda: app._session_id is not None)
        editor = app.query_one(ChatTextArea)
        editor.text = "压缩前的对话"
        await pilot.press("enter")
        await _wait(lambda: not app._busy)
        root = tmp_path / ".kama" / "sessions" / str(app._session_id)
        editor.text = "/compact"
        # S7 自动补全先消费 Enter；Esc 收起候选后才提交精确命令。
        await pilot.pause()
        await pilot.press("escape")
        await pilot.press("enter")
        await _wait(lambda: len(list(root.glob("thread_*.bak"))) == 1)
        await pilot.pause()
        rows = [json.loads(line) for line in (root / "thread.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert rows[0]["content"] == "offline test complete"
        assert rows[1]["role"] == "assistant"
        editor.text = "压缩后继续"
        await pilot.press("enter")
        await _wait(lambda: not app._busy)
        await pilot.pause()
        rows = [json.loads(line) for line in (root / "thread.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 4 and rows[2]["content"] == "压缩后继续"
        await pilot.press("ctrl+q")
