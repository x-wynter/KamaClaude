# 文件订阅测试：tmp_path 隔离磁盘数据，async with 控制文件打开和关闭。

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from kama_claude.core.bus.events import RunFinishedEvent, RunStartedEvent
from kama_claude.core.events.bus import EventBus
from kama_claude.core.events.writer import EventWriter


# 功能：一个事件写成一条可解析的 JSONL 记录。
# 设计：退出文件上下文后读回，再检查关键字段而非空白排版。
async def test_event_writer_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = RunStartedEvent(run_id="run-1", goal="test goal", ts="2026-05-11T00:00:00Z")

    async with EventWriter(path) as writer:
        await writer.handle(event)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "run.started"
    assert data["run_id"] == "run-1"
    assert data["goal"] == "test goal"


# 功能：写入器能创建缺失的多层父目录。
# 设计：在 tmp_path 下指定尚不存在的目录层级。
async def test_event_writer_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "abc123" / "events.jsonl"
    event = RunStartedEvent(run_id="abc123", goal="test", ts="2026-05-11T00:00:00Z")

    async with EventWriter(path) as writer:
        await writer.handle(event)

    assert path.exists()


# 功能：多次 handle 追加记录而不覆盖前一条。
# 设计：写入不同类型事件，再逐行校验它们的顺序。
async def test_event_writer_appends_multiple_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    async with EventWriter(path) as writer:
        await writer.handle(RunStartedEvent(run_id="r1", goal="g1", ts="2026-05-11T00:00:00Z"))
        finished = RunFinishedEvent(
            run_id="r1", status="success", steps=2, ts="2026-05-11T00:00:01Z"
        )
        await writer.handle(finished)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "run.started"
    assert json.loads(lines[1])["type"] == "run.finished"


# 功能：总线发布可触发文件写入订阅者。
# 设计：通过 subscribe 和 publish 间接调用，验证真实接线关系。
async def test_event_writer_subscribe_via_bus(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    bus = EventBus()
    event = RunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")

    async with EventWriter(path) as writer:
        writer.subscribe(bus)
        await bus.publish(event)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "r1"


# 功能：未打开文件时 handle 可直接返回。
# 设计：只构造对象，不进入 async with，覆盖空句柄路径。
async def test_event_writer_handle_when_not_open_is_noop(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    event = RunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")
    await writer.handle(event)  # _file is None, should not raise


# 功能：文件写入失败会记录日志而不向调用方抛出。
# 设计：主动关闭句柄触发 ValueError，用 caplog 收集错误日志。
async def test_event_writer_oserror_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "events.jsonl"
    event = RunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")

    # caplog 是 pytest 日志捕获夹具；这里只捕获指定日志器的 ERROR 及以上级别。
    with caplog.at_level(logging.ERROR, logger="kama_claude.core.events.writer"):
        async with EventWriter(path) as writer:
            assert writer._file is not None
            writer._file.close()
            # _file 仍非 None 但已关闭，文本文件 write 会抛 ValueError
            await writer.handle(event)

    # any 遇到一个匹配就返回 True；生成器逐条检查日志，不额外创建列表。
    assert any("failed to write" in r.message for r in caplog.records)
