# ═══ 文件职责：Anthropic SDK 的适配层 ═══
# 协作关系：Loop 传入消息和工具说明；本文件发起流式请求，发布事件并返回统一响应。
# 阅读路线：先看构造函数，再按 chat 的请求准备、流式接收、结果整理阅读。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx

from kama_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats

_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-opus-4-7": 200_000,
}

_MAX_STREAM_RETRIES = 3
_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)

log = logging.getLogger(__name__)


# 返回指定模型的最大 context window token 数
def _context_window(model: str) -> int:
    return _MODEL_CONTEXT_WINDOWS.get(model, 200_000)


# 括号内相邻字符串由 Python 自动拼接，不会自动插入换行。
# 这是系统提示，与 messages 中逐轮积累的对话内容分开传给 SDK。
_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)


# ── 函数：_now ──
# 用途：返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 类：AnthropicProvider ──
# 职责：把项目的模型接口转换为 Anthropic SDK 请求。
# 字段说明：
#   _client：真实或测试用 SDK 客户端；_model：本次请求使用的模型名称。
class AnthropicProvider:
    # ── 初始化：__init__ ──
    # 用途：初始化 Anthropic 客户端；client 可在测试时注入以跳过 API key 检查
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

    # ── 函数：chat ──
    # 用途：流式调用 Anthropic API，逐 token 发布事件并返回 LlmResponse
    # 输入：完整历史、工具说明、事件总线和任务 ID；工具说明不包含可执行函数。
    # 输出：完整响应交给 Loop；中途文本和最终用量通过 bus 发送给观察者。
    # 流式调用 Anthropic API，逐 token 发布事件并返回 LlmResponse；网络中断时自动重试
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        # 接受 S3 接口的轮次参数；本实现不使用它选模型或调度任务，trace 包装器会使用。
        step: int = 0,
        system: str | None = None,  # S4 调用方可注入会话指令；None 时用默认提示。
    ) -> LlmResponse:
        # 步骤 1：发布本次模型选择事件。
        await bus.publish(
            LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now())
        )

        # 步骤 2：准备系统提示和工具说明。
        # 系统提示使用块列表格式；cache_control 是发送给服务的请求字段。
        # 这里没有把模型缓存保存在本地 Python 字典中。
        system_blocks: list[dict[str, object]] = [
            {
                "type": "text",
                "text": system or _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
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

        # 步骤 3：整理 SDK 请求参数。
        # kwargs 是普通字典名，不是关键字；稍后 ** 展开相当于 model=... 等参数。
        # max_tokens 是本次请求的输出额度，和 AgentLoop 的 max_steps 含义不同。
        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 8192,
            "system": system_blocks,
            "messages": messages,
        }
        # 空列表不传 tools 参数；非空时追加到请求字典。
        if tools:
            kwargs["tools"] = tools

        # 步骤 4：建立文本片段列表并打开流式请求。
        # 保存流式片段，既用于实时发布，也用于流结束后拼成完整文本。
        text_parts: list[str] = []
        final_message: Any = None

        # 三次总尝试，只等待 1、2 秒；每次 async with 负责关闭该次流。
        # 每次清空文本缓存，最终只返回成功尝试的完整响应。
        for attempt in range(1, _MAX_STREAM_RETRIES + 1):
            text_parts = []
            try:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        # 只广播首次片段以免重复；重试完整文本进入返回值，
                        # TUI 的实时文本可能仍停留在首次尝试的残片。
                        if attempt == 1:
                            await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                        text_parts.append(text)
                    final_message = await stream.get_final_message()
                break  # success
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                if attempt == _MAX_STREAM_RETRIES:
                    log.error(
                        "stream failed after %d attempts run_id=%s step=%d: %s",
                        _MAX_STREAM_RETRIES,
                        run_id,
                        step,
                        exc,
                    )
                    raise
                delay = _RETRY_BACKOFF_S[attempt - 1]
                log.warning(
                    "stream dropped (attempt %d/%d) run_id=%s step=%d: %s — retrying in %.0fs",
                    attempt,
                    _MAX_STREAM_RETRIES,
                    run_id,
                    step,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        assert final_message is not None

        # 步骤 7：整理用量，发布统计事件。
        # 流结束后读取 SDK 返回的对象属性；这里不是从字典用 [] 取字段。
        usage = final_message.usage
        # getattr(obj, name, default) 读取可选属性；or 0 也将 None 归零。
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0
        # 官方比例未计入缓存读写 token，不能当作完整窗口占用率。
        context_pct = usage.input_tokens / _context_window(self._model)

        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
                ts=_now(),
            )
        )

        # 步骤 8：提取工具调用，转换为项目内部结构。
        tool_calls: list[ToolCallBlock] = []
        # 筛选 tool_use 块并转换为项目类型；provider 本身不执行这些工具。
        thinking_blocks: list[dict[str, object]] = []
        for block in final_message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    # dict(block.input) 转成普通字典，避免把 SDK 的响应类型传进工具层。
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )
            elif block.type == "thinking":
                # thinking blocks must be passed back verbatim in subsequent requests
                thinking_blocks.append(
                    {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
                )

        # 步骤 9：返回统一响应，供 Loop 判断下一步。
        # 把供应商专有响应转换为项目内部类型，Loop 因此不需要了解 SDK 的细节。
        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            # join 一次拼接全部文本片段；空列表会得到空字符串。
            text="".join(text_parts),
            thinking_blocks=thinking_blocks,
            usage=UsageStats(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
            ),
        )
