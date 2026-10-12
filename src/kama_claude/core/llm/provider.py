# Anthropic 适配器：发送流式请求、发布文本/用量事件、统一返回 LlmResponse。

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import anthropic

from kama_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats

_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


class AnthropicProvider:
    # 初始化 Anthropic 客户端；client 可在测试时注入以跳过 API key 检查
    def __init__(self, model: str, client: Any = None) -> None:
        # 测试传入假客户端就可跳过凭据检查；此处固定使用 Anthropic SDK。
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY not set")
            self._client: Any = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self._client = client
        self._model = model

    # 流式调用 Anthropic API，逐 token 发布事件并返回 LlmResponse
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
    ) -> LlmResponse:
        await bus.publish(
            LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now())
        )

        system: list[dict[str, object]] = [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]

        # list 只复制外层列表；后面再复制最后一个字典，避免修改注册表的 schema。
        tools: list[dict[str, object]] = list(tool_schemas)
        if tools:
            last = dict(tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            # [:-1] 取最后一项之前的元素；拼接得到新的工具列表。
            tools = tools[:-1] + [last]

        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        text_parts: list[str] = []

        # **kwargs 把字典展开为关键字参数；异步上下文管理器负责流连接生命周期。
        async with self._client.messages.stream(**kwargs) as stream:
            # async for 等待流的下一块文本；事件虽名为 token，块未必是单个分词 token。
            async for text in stream.text_stream:
                await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                text_parts.append(text)
            # 完整消息还包含工具调用和用量，不能只用已输出的文本代替。
            final_message = await stream.get_final_message()

        usage = final_message.usage
        # getattr(obj, name, default) 读取可选属性；or 0 也将 None 归零。
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0

        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                ts=_now(),
            )
        )

        tool_calls: list[ToolCallBlock] = []
        # 筛选 tool_use 块并转换为项目类型；provider 本身不执行这些工具。
        for block in final_message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            # join 一次拼接全部文本片段；空列表会得到空字符串。
            text="".join(text_parts),
            usage=UsageStats(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
            ),
        )
