"""S2 Windows 回归：探测不能杀进程，后台启动不弹窗，中文历史可回放。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from kama_claude.cli.commands import core
from kama_claude.core.app import CoreApp
from kama_claude.core.config import KamaConfig


# 启动可控子进程而不是查询 pytest 自己，旧实现误杀时也不会杀掉测试主进程。
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process API")
def test_pid_probe_does_not_terminate_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "core.pid"
    monkeypatch.setattr(core, "_PID_FILE", pid_file)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        assert core._running_pid() == proc.pid
        assert proc.poll() is None
        proc.terminate()
        proc.wait(timeout=5)
        assert core._running_pid() is None
        assert not pid_file.exists()
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=5)


# 参数化让三个无效输入走同一测试；都应删除过期文件，不调用任何进程信号 API。
@pytest.mark.parametrize("value", ["bad", "0", "-1"])
def test_invalid_pid_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    pid_file = tmp_path / "core.pid"
    pid_file.write_text(value, encoding="utf-8")
    monkeypatch.setattr(core, "_PID_FILE", pid_file)
    kill = MagicMock()
    monkeypatch.setattr(core.os, "kill", kill)
    assert core._running_pid() is None
    assert not pid_file.exists()
    kill.assert_not_called()


# 用替身截获 Popen 参数，验证启动策略而不留下后台 daemon。
@pytest.mark.skipif(sys.platform != "win32", reason="Windows startup flags")
def test_background_start_hides_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_PID_FILE", tmp_path / "core.pid")
    monkeypatch.setattr(core, "_ping_check", AsyncMock(side_effect=ConnectionRefusedError))
    popen = MagicMock(return_value=MagicMock(pid=1234))
    monkeypatch.setattr(core.subprocess, "Popen", popen)
    core.cmd_core_start(KamaConfig())
    assert popen.call_args.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert popen.call_args.kwargs["start_new_session"] is False


# 直接调用回放函数：文件里保留中文原文，替身 writer 记录真正发出的 NDJSON 字节。
async def test_replay_reads_utf8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "events.jsonl"
    event = {"type": "run.started", "run_id": "r1", "goal": "读取中文文件：你好世界"}
    path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr("kama_claude.core.app.events_file", lambda run_id: path)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    count = await CoreApp()._replay_events("r1", cast(asyncio.StreamWriter, writer), ["run.*"])
    assert count == 1
    assert json.loads(writer.write.call_args.args[0])["event"] == event
    writer.drain.assert_awaited_once()
