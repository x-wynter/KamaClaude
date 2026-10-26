# 真实 Textual 键盘 → TCP → Skill → 子 Loop → 事件及历史落盘；仅替换模型。
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kama_claude.tui.app import ChatTextArea, KamaTuiApp, PermissionSelect, SlashCompleteWidget
from tests.integration.test_s5_tui_offline import _wait


# 功能：斜杠补全提交编排 Skill，子任务事件闭合，主会话可继续输入。
# 设计：检查实际磁盘工具结果及 TUI 子任务索引，排除只有候选框可显示的假闭环。
async def test_skill_subagent_tui(running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path) -> None:
    app = KamaTuiApp('127.0.0.1', free_port)
    async with app.run_test(size=(110, 40)) as pilot:
        await _wait(lambda: app._session_id is not None)
        editor = app.query_one(ChatTextArea)
        await pilot.press('/', 'o', 'r', 'c')
        await pilot.pause()
        assert app.query_one(SlashCompleteWidget).has_selection()
        await pilot.press('tab')
        await pilot.pause()
        assert editor.text == '/orchestrate '
        editor.text += 's7 中文目标'
        await pilot.press('enter')
        await _wait(lambda: len(app.query(PermissionSelect)) == 1)
        await pilot.press('y')
        await _wait(lambda: not app._busy)
        await pilot.pause()
        root = tmp_path / '.kama/sessions' / str(app._session_id)
        rows = [json.loads(x) for x in (root / 'thread.jsonl').read_text(encoding='utf-8').splitlines()]
        assert rows[0]['content'] == '/orchestrate s7 中文目标'
        assert any('tool_result' in str(row) and 'offline test complete' in str(row) for row in rows)
        event_files = list((root / 'runs').glob('*/events.jsonl'))
        assert len(event_files) == 2
        events = [json.loads(x) for f in event_files for x in f.read_text(encoding='utf-8').splitlines()]
        assert any(e['type'] == 'subagent.started' for e in events)
        assert any(e['type'] == 'subagent.finished' and e['status'] == 'success' for e in events)
        assert not app._subagent_run_ids and not app._pending_tool_blocks
        editor.text = '继续'
        await pilot.press('enter')
        await _wait(lambda: not app._busy)
        assert '继续' in (root / 'thread.jsonl').read_text(encoding='utf-8')
        await pilot.press('ctrl+q')
