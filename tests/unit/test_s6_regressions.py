# S6 行为回归：真实 Runner/Store/Loop，模型边界全部使用替身。
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kama_claude.core.bus.envelope import HandlerError
from kama_claude.core.config import KamaConfig
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.provider import AnthropicProvider
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats
from kama_claude.core.runner import AgentRunner
from kama_claude.core.session.manager import SessionManager
from kama_claude.core.session.store import SessionStore
from tests.unit.test_llm_provider import FakeStream, _make_final


# 功能：自动摘要及其后的消息跨 run 保存，下一轮确实读到摘要。
# 设计：旧历史多于压缩后的消息，直接暴露按旧 prefill_len 切片丢失内容的问题。
async def test_auto_compaction_persists_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = KamaConfig()
    config.compaction.auto_threshold = 0.5
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=[
        LlmResponse(stop_reason="tool_use", tool_calls=[ToolCallBlock("t", "list_dir", {"path": "."})], usage=UsageStats(100, 10, context_pct=0.8)),
        LlmResponse(stop_reason="end_turn", text="中文摘要", usage=UsageStats(100, 3)),
        LlmResponse(stop_reason="end_turn", text="本轮结束"),
        LlmResponse(stop_reason="end_turn", text="接续完成"),
    ])
    bus = EventBus()
    store = SessionStore(tmp_path / "sessions")
    manager = SessionManager(store, lambda: AgentRunner(config, bus=bus, provider=provider), bus)
    session = await manager.create("chat")
    for _ in range(4):
        store.append_message(session.id, "user", "旧问题")
        store.append_message(session.id, "assistant", "旧回答")
    await manager.send_message(session.id, "继续")
    history = store.read_messages(session.id)
    assert history[0]["content"] == "中文摘要"
    assert history[-1]["content"][0]["text"] == "本轮结束"
    assert len(list(store.session_dir(session.id).glob("thread_*.bak"))) == 1
    await manager.send_message(session.id, "再继续")
    assert provider.chat.call_args.kwargs["messages"][0]["content"] == "中文摘要"


# 功能：max_tokens 的未完成工具调用只补错误结果，不执行工具。
# 设计：write_file 若被误执行会留下文件；同时检查下一次模型输入中的配对 ID。
async def test_max_tokens_balances_without_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=[
        LlmResponse(stop_reason="max_tokens", tool_calls=[ToolCallBlock("partial", "write_file", {"path": "bad.txt", "content": "bad"})]),
        LlmResponse(stop_reason="end_turn", text="recovered"),
    ])
    result = await AgentRunner(KamaConfig(), provider=provider, runs_dir=tmp_path / "runs").run_and_capture("test")
    assert result.status == "success"
    assert not (tmp_path / "bad.txt").exists()
    block = provider.chat.call_args.kwargs["messages"][2]["content"][0]
    assert block["tool_use_id"] == "partial" and block["is_error"]


# 功能：手动压缩取消或失败不改 thread，释放锁后可以连续压缩并保留独立备份。
# 设计：用 Event 精确停在模型等待点，再取消任务；以文件字节和备份数量验证持久行为。
async def test_manual_cancel_failure_and_backups(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    provider = MagicMock()
    entered = asyncio.Event()

    # 通知测试已经持锁等待模型，之后只由取消结束。
    async def waiting(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    provider.chat = AsyncMock(side_effect=waiting)
    manager = SessionManager(store, MagicMock(), EventBus(), provider)
    session = await manager.create("chat")
    store.append_message(session.id, "user", "original")
    path = store.session_dir(session.id) / "thread.jsonl"
    original = path.read_bytes()
    task = asyncio.create_task(manager.compact(session.id))
    await entered.wait()
    with pytest.raises(HandlerError, match="busy"):
        await manager.compact(session.id)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.read_bytes() == original
    provider.chat = AsyncMock(return_value=LlmResponse(stop_reason="end_turn", text=""))
    with pytest.raises(HandlerError, match="compaction failed"):
        await manager.compact(session.id)
    assert path.read_bytes() == original
    provider.chat = AsyncMock(return_value=LlmResponse(stop_reason="end_turn", text="summary"))
    await manager.compact(session.id, "focus")
    assert "focus" in str(provider.chat.call_args)
    await manager.compact(session.id)
    assert len(list(path.parent.glob("thread_*.bak"))) == 2


@pytest.mark.parametrize("failures", [1, 3])
# 功能：流失败最多尝试三次，成功时返回完整新响应，失败耗尽时传播异常。
# 设计：模拟 SDK 流入口的网络错误，并替换 sleep 验证实际等待序列，禁止网络访问。
async def test_stream_retry_budget(failures: int, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.messages.stream.side_effect = [httpx.ReadError("offline")] * failures + [FakeStream(["complete"], _make_final())]
    sleep = AsyncMock()
    monkeypatch.setattr("kama_claude.core.llm.provider.asyncio.sleep", sleep)
    provider = AnthropicProvider("test", client=client)
    if failures == 3:
        with pytest.raises(httpx.ReadError):
            await provider.chat([], [], EventBus(), "r")
        assert client.messages.stream.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [1.0, 2.0]
    else:
        response = await provider.chat([], [], EventBus(), "r")
        assert response.text == "complete"
        assert client.messages.stream.call_count == 2
        sleep.assert_awaited_once_with(1.0)
