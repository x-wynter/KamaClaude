# S3 Windows 编码回归：磁盘内容用 UTF-8，中文和非本地代码页字符均应完整往返。
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kama_claude.cli.commands.trace import cmd_trace
from kama_claude.core.config import KamaConfig
from kama_claude.core.task.manager import TaskManager
from kama_claude.core.trace.record import TraceRecord
from kama_claude.core.trace.writer import TraceWriter


# 功能：任务创建、重载、列表和完成清依赖路径全部保留 UTF-8 内容。
# 设计：加入 emoji 防止默认 GBK 读写碰巧通过，重建 Manager 同时验证编号续接。
def test_task_utf8_roundtrip_and_dependency_cleanup(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)
    first = manager.create("读取源码🧭", "中文说明")
    second = manager.create("写笔记📝", blocked_by=[first.id])
    manager = TaskManager(tmp_path)
    assert manager.get(first.id).subject == "读取源码🧭"
    assert "写笔记📝" in manager.format_list()
    manager.update(first.id, status="completed")
    assert manager.get(second.id).blocked_by == []
    assert manager.create("下一项").id == second.id + 1
    data = json.loads((tmp_path / f"task_{second.id}.json").read_bytes().decode("utf-8"))
    assert data["subject"] == "写笔记📝"


# 功能：TraceWriter 排空队列后，CLI 能以 UTF-8 读取含中文和方向箭头的记录。
# 设计：真实文件和 raw CLI 输出构成写读回归，不依赖操作系统默认编码。
async def test_trace_utf8_writer_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    await writer.start()
    writer.emit(TraceRecord(
        ts="2026-10-13T00:00:00+00:00", direction="CORE→LLM", layer="llm",
        kind="api_call", run_id="中文", data={"goal": "阅读🧭"},
    ))
    await writer.stop()
    config = KamaConfig()
    config.trace.file = str(path)
    cmd_trace("中文", config, raw=True)
    record = json.loads(capsys.readouterr().out)
    assert record["direction"] == "CORE→LLM"
    assert record["data"]["goal"] == "阅读🧭"


# 功能：同一文件混合旧 GBK 和新 UTF-8 记录时均能读取。
# 设计：方向箭头和中文覆盖真实旧日志，emoji 保证新行必须按 UTF-8 解码。
def test_trace_mixed_encodings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "mixed.jsonl"
    old = TraceRecord(ts="now", direction="CLIENT→CORE", layer="ipc",
                      kind="command", data={"goal": "旧中文"})
    new = old.model_copy(update={"data": {"goal": "新中文🧭"}})
    path.write_bytes(old.model_dump_json().encode("gbk") + b"\n"
                     + new.model_dump_json().encode("utf-8") + b"\n")
    config = KamaConfig()
    config.trace.file = str(path)
    cmd_trace(None, config, raw=True)
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["data"]["goal"] for line in lines] == ["旧中文", "新中文🧭"]
