# Anthropic 适配器：发送流式请求、发布文本/用量事件、统一返回 LlmResponse。

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import anthropic

from kama_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats

# 括号内相邻字符串由 Python 自动拼接，不会自动插入换行。
# 这是系统提示，与 messages 中逐轮积累的对话内容分开传给 SDK。
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
            # os.environ 类似字符串字典；get 找不到返回 None，空字符串也无法通过下方判断。
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                # 缺少凭据在创建 provider 时就退出；此时还没有发出模型网络请求。
                raise SystemExit("ANTHROPIC_API_KEY not set")
            self._client: Any = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self._client = client
        # 模型名原样保存；这个类没有按名称前缀选择其他供应商的代码。
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

        # 系统提示使用块列表格式；cache_control 是发送给服务的请求字段。
        # 这里没有把模型缓存保存在本地 Python 字典中。
        system: list[dict[str, object]] = [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]

        # list 只复制外层列表；后面再复制最后一个字典，避免修改注册表的 schema。
        tools: list[dict[str, object]] = list(tool_schemas)
        # 空列表不传 tools 参数；非空时追加到请求字典。
        if tools:
            # -1 读取最后一项；dict(existing) 创建浅副本，下面只新增顶层键。
            last = dict(tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            # [:-1] 取最后一项之前的元素；拼接得到新的工具列表。
            tools = tools[:-1] + [last]

        # kwargs 是普通字典名，不是关键字；稍后 ** 展开相当于 model=... 等参数。
        # max_tokens 是本次请求的输出额度，和 AgentLoop 的 max_steps 含义不同。
        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
        }
        # 空列表不传 tools 参数；非空时追加到请求字典。
        if tools:
            kwargs["tools"] = tools

        # 保存流式片段，既用于实时发布，也用于流结束后拼成完整文本。
        text_parts: list[str] = []

        # **kwargs 把字典展开为关键字参数；异步上下文管理器负责流连接生命周期。
        async with self._client.messages.stream(**kwargs) as stream:
            # async for 等待流的下一块文本；事件虽名为 token，块未必是单个分词 token。
            async for text in stream.text_stream:
                await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                # append 不会丢弃片段里的空格；最终 join 按到达顺序原样拼接。
                text_parts.append(text)
            # 完整消息还包含工具调用和用量，不能只用已输出的文本代替。
            final_message = await stream.get_final_message()

        # 流结束后读取 SDK 返回的对象属性；这里不是从字典用 [] 取字段。
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
                    # dict(block.input) 转成普通字典，避免把 SDK 的响应类型传进工具层。
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        # 把供应商专有响应转换为项目内部类型，Loop 因此不需要了解 SDK 的细节。
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
