# 适配器测试：FakeStream 和 MagicMock 模拟 SDK，保留流式接口而不访问 API。

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.provider import AnthropicProvider
from kama_claude.core.llm.types import LlmResponse

# --- helpers -----------------------------------------------------------------


def _make_usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
    cache_create: int = 0,
) -> MagicMock:
    # MagicMock 可动态设置属性；这里只模拟用量对象字段，并不执行 SDK。
    u = MagicMock()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_read_input_tokens = cache_read
    u.cache_creation_input_tokens = cache_create
    return u


def _make_final(
    stop_reason: str = "end_turn",
    content: list[MagicMock] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
) -> MagicMock:
    # 构造最终消息替身，字段名与 provider 读取的 SDK 属性对应。
    msg = MagicMock()
    msg.stop_reason = stop_reason
    msg.content = content or []
    msg.usage = _make_usage(input_tokens, output_tokens, cache_read)
    return msg


class FakeStream:
    """Minimal async context manager that fakes the anthropic streaming interface."""

    def __init__(self, texts: list[str], final: MagicMock) -> None:
        self._texts = texts
        self._final = final

    # async with 进入时调用这个方法，返回的 self 绑定到 as stream。
    async def __aenter__(self) -> FakeStream:
        return self

    # async with 退出时调用；替身无网络资源，所以这里不执行清理。
    async def __aexit__(self, *args: object) -> None:
        pass

    # property 让 text_stream 像属性一样读取；返回的仍是异步生成器。
    @property
    def text_stream(self):  # type: ignore[return]
        # 含 yield 的 async def 定义异步生成器；调用它得到可供 async for 迭代的对象。
        async def _gen():
            for t in self._texts:
                # yield 每次交出一段文本，下一次 async for 迭代再继续。
                yield t

        return _gen()

    async def get_final_message(self) -> MagicMock:
        return self._final


def _make_provider(
    texts: list[str] | None = None,
    stop_reason: str = "end_turn",
    content: list[MagicMock] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
) -> tuple[AnthropicProvider, MagicMock]:
    final = _make_final(stop_reason, content, input_tokens, output_tokens, cache_read)
    client = MagicMock()
    # 设置普通 stream 调用的返回值为异步上下文对象；不是把 stream 改成协程。
    client.messages.stream.return_value = FakeStream(texts or [], final)
    # 把 client 注入真实适配器，只替换外部 SDK，不替换被测试的 chat 逻辑。
    return AnthropicProvider(model="test-model", client=client), client


async def _chat(
    provider: AnthropicProvider,
    messages: list[dict[str, object]] | None = None,
    tool_schemas: list[dict[str, object]] | None = None,
) -> tuple[LlmResponse, list[BaseModel]]:
    collected: list[BaseModel] = []
    bus = EventBus()

    async def _collect(e: BaseModel) -> None:
        collected.append(e)

    bus.subscribe(_collect)
    result = await provider.chat(
        messages=messages or [],
        tool_schemas=tool_schemas or [],
        bus=bus,
        run_id="r1",
    )
    return result, collected


# --- tests -------------------------------------------------------------------


# 功能：模型选择事件包含配置的模型、策略和任务 ID。
# 设计：使用假客户端执行真实 provider.chat，并检查订阅到的模型事件。
async def test_model_selected_event_published() -> None:
    provider, _ = _make_provider()
    _, events = await _chat(provider)
    sel = [e for e in events if e.type == "llm.model_selected"]  # type: ignore[attr-defined]
    assert len(sel) == 1
    assert sel[0].model == "test-model"  # type: ignore[attr-defined]
    assert sel[0].strategy == "static"  # type: ignore[attr-defined]
    assert sel[0].run_id == "r1"  # type: ignore[attr-defined]


# 功能：每个流式文本片段对应一个事件。
# 设计：设定两个含空格的片段，检查条数、内容和顺序。
async def test_token_events_published_per_chunk() -> None:
    provider, _ = _make_provider(texts=["Hello", " world"])
    _, events = await _chat(provider)
    tokens = [e for e in events if e.type == "llm.token"]  # type: ignore[attr-defined]
    assert len(tokens) == 2
    assert tokens[0].token == "Hello"  # type: ignore[attr-defined]
    assert tokens[1].token == " world"  # type: ignore[attr-defined]


# 功能：SDK 用量字段正确映射到用量事件。
# 设计：设置不同输入、输出和缓存计数，防止字段混淆。
async def test_usage_event_published_after_stream() -> None:
    provider, _ = _make_provider(input_tokens=200, output_tokens=75, cache_read=150)
    _, events = await _chat(provider)
    usage_events = [e for e in events if e.type == "llm.usage"]  # type: ignore[attr-defined]
    assert len(usage_events) == 1
    ue = usage_events[0]
    assert ue.input_tokens == 200  # type: ignore[attr-defined]
    assert ue.output_tokens == 75  # type: ignore[attr-defined]
    assert ue.cache_read_input_tokens == 150  # type: ignore[attr-defined]


# 功能：模型选择在前，用量在文本流之后。
# 设计：检查整个事件类型序列的首尾，中间应包含文本事件。
async def test_event_order_model_selected_first_usage_last() -> None:
    provider, _ = _make_provider(texts=["hi"])
    _, events = await _chat(provider)
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert types[0] == "llm.model_selected"
    assert "llm.token" in types
    assert types[-1] == "llm.usage"


# 功能：完整 SDK 消息中的调用块转为内部工具请求。
# 设计：MagicMock 提供指定字段，逐个比较 id、name、input。
async def test_tool_use_parsed_from_final_message() -> None:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_01"
    tool_block.name = "read_file"
    tool_block.input = {"path": "README.md"}
    provider, _ = _make_provider(stop_reason="tool_use", content=[tool_block])
    result, _ = await _chat(provider)
    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "toolu_01"
    assert tc.name == "read_file"
    assert tc.input == {"path": "README.md"}


# 功能：无工具内容的结束响应返回空调用列表。
# 设计：最终消息不含工具块，检查 stop_reason 和工具列表。
async def test_end_turn_produces_no_tool_calls() -> None:
    provider, _ = _make_provider(stop_reason="end_turn")
    result, _ = await _chat(provider)
    assert result.stop_reason == "end_turn"
    assert result.tool_calls == []


# 功能：流式片段按顺序合并为完整回答。
# 设计：三个不同片段可识别丢失或顺序错误。
async def test_text_accumulated_from_tokens() -> None:
    provider, _ = _make_provider(texts=["foo", "bar", "baz"])
    result, _ = await _chat(provider)
    assert result.text == "foobarbaz"


# 功能：未提供客户端且缺少密钥时退出。
# 设计：monkeypatch 临时移除环境变量，pytest.raises 检查退出异常。
async def test_missing_api_key_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest 提供的 monkeypatch 会在测试结束后恢复环境；raising=False 接受变量原本不存在。
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        AnthropicProvider(model="any")


# 功能：空文本流不产生文本事件。
# 设计：同时检查返回文本为空，覆盖零次异步迭代路径。
async def test_no_tokens_when_response_is_empty() -> None:
    provider, _ = _make_provider(texts=[])
    result, events = await _chat(provider)
    tokens = [e for e in events if e.type == "llm.token"]  # type: ignore[attr-defined]
    assert tokens == []
    assert result.text == ""
