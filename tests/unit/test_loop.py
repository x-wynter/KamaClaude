# 循环测试：假 provider 按预设顺序返回响应，验证工具反馈与终止条件。

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from kama_claude.core.context import ExecutionContext
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock
from kama_claude.core.loop import AgentLoop
from kama_claude.core.tools.base import BaseTool, ToolResult
from kama_claude.core.tools.registry import ToolRegistry

# --- stubs -------------------------------------------------------------------


class _MockProvider:
    """Returns canned responses in order; raises exc immediately if given."""

    def __init__(
        self,
        responses: list[LlmResponse],
        exc: BaseException | None = None,
    ) -> None:
        # iter 将列表转为有当前位置的迭代器；每次 chat 用 next 取下一份预设响应。
        self._responses = iter(responses)
        self._exc = exc

    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
    ) -> LlmResponse:
        if self._exc is not None:
            raise self._exc
        # 没有剩余响应时会抛 StopIteration；因此测试必须准备足够的模型轮次。
        return next(self._responses)


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echoes msg"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        return ToolResult(content=str(params["msg"]))


class _FailTool(BaseTool):
    name = "fail"
    description = "Always raises"
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        raise RuntimeError("tool error")


# --- helpers -----------------------------------------------------------------


def _ctx(max_steps: int = 5) -> ExecutionContext:
    return ExecutionContext(run_id="r1", goal="test goal", max_steps=max_steps)


def _tc(name: str = "echo", inp: dict[str, object] | None = None, uid: str = "t1") -> ToolCallBlock:
    return ToolCallBlock(id=uid, name=name, input=inp or {"msg": "hi"})


def _make_loop(
    provider: _MockProvider,
    registry: ToolRegistry | None = None,
    bus: EventBus | None = None,
) -> tuple[AgentLoop, EventBus]:
    # 允许传入已订阅的总线；返回 (loop, b) 元组方便测试同时驱动和观察。
    b = bus or EventBus()
    return AgentLoop(provider, registry or ToolRegistry(), b), b  # type: ignore[arg-type]


async def _events(bus: EventBus) -> list[BaseModel]:
    collected: list[BaseModel] = []

    async def _h(e: BaseModel) -> None:
        collected.append(e)

    # _h 闭包持有 collected；返回列表后，后续回调仍可向同一列表追加元素。
    bus.subscribe(_h)
    return collected


# --- tests -------------------------------------------------------------------


# 功能：模型第一轮结束时任务成功。
# 设计：只提供一份响应，检查状态和实际轮数。
async def test_end_turn_marks_success() -> None:
    provider = _MockProvider([LlmResponse(stop_reason="end_turn", text="done")])
    # 元组解包取出 Loop；下划线变量表示这次不使用返回的总线。
    loop, _ = _make_loop(provider)
    ctx = _ctx()
    await loop.run(ctx)
    assert ctx.status == "success"
    assert ctx.step == 1


# 功能：连续工具调用达到上限时失败。
# 设计：预置多于上限的响应，确认循环停在设定步数。
async def test_max_steps_marks_failed() -> None:
    tc = _tc("unknown", {})
    provider = _MockProvider([LlmResponse(stop_reason="tool_use", tool_calls=[tc])] * 10)
    # 元组解包取出 Loop；下划线变量表示这次不使用返回的总线。
    loop, _ = _make_loop(provider)
    ctx = _ctx(max_steps=2)
    await loop.run(ctx)
    assert ctx.status == "failed"
    assert ctx.reason == "exceeded_max_steps"
    assert ctx.step == 2


# 功能：一次工具调用后模型可正常结束。
# 设计：用两份预设响应模拟 act 后继续 plan 的流程。
async def test_tool_use_then_end_turn_marks_success() -> None:
    provider = _MockProvider([
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc()]),
        LlmResponse(stop_reason="end_turn", text="summary"),
    ])
    registry = ToolRegistry()
    registry.register(_EchoTool())
    loop, _ = _make_loop(provider, registry)
    ctx = _ctx()
    await loop.run(ctx)
    assert ctx.status == "success"
    assert ctx.step == 2


# 功能：工具输出按调用 ID 加入对话历史。
# 设计：使用确定文本的回显工具，检查第三条消息中的结果块。
async def test_tool_result_appended_to_context() -> None:
    provider = _MockProvider([
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc(inp={"msg": "hello"})]),
        LlmResponse(stop_reason="end_turn"),
    ])
    registry = ToolRegistry()
    registry.register(_EchoTool())
    loop, _ = _make_loop(provider, registry)
    ctx = _ctx()
    await loop.run(ctx)
    # messages: [goal, assistant(tool_use), user(tool_result), assistant(end_turn)]
    tool_result_msg = ctx.messages[2]
    assert tool_result_msg["role"] == "user"
    block = tool_result_msg["content"][0]  # type: ignore[index]
    assert block["tool_use_id"] == "t1"
    assert block["content"] == "hello"


# 功能：工具异常不会直接终止 Agent 循环。
# 设计：第一轮工具失败，第二轮模型结束，检查成功及两步计数。
async def test_tool_failure_loop_continues_to_success() -> None:
    provider = _MockProvider([
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc("fail", {})]),
        LlmResponse(stop_reason="end_turn", text="handled error"),
    ])
    registry = ToolRegistry()
    registry.register(_FailTool())
    loop, _ = _make_loop(provider, registry)
    ctx = _ctx()
    await loop.run(ctx)
    assert ctx.status == "success"
    assert ctx.step == 2


# 功能：工具失败在历史消息中标记 is_error。
# 设计：直接检查工具结果块，而不只检查最终任务状态。
async def test_tool_failure_result_is_error_in_context() -> None:
    provider = _MockProvider([
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc("fail", {})]),
        LlmResponse(stop_reason="end_turn"),
    ])
    registry = ToolRegistry()
    registry.register(_FailTool())
    loop, _ = _make_loop(provider, registry)
    ctx = _ctx()
    await loop.run(ctx)
    tool_result_msg = ctx.messages[2]
    block = tool_result_msg["content"][0]  # type: ignore[index]
    assert block.get("is_error") is True


# 功能：取消会标记失败且继续向上传播。
# 设计：假模型抛 CancelledError，既检查异常也检查上下文。
async def test_cancelled_error_marks_failed_and_reraises() -> None:
    provider = _MockProvider([], exc=asyncio.CancelledError())
    # 元组解包取出 Loop；下划线变量表示这次不使用返回的总线。
    loop, _ = _make_loop(provider)
    ctx = _ctx()
    with pytest.raises(asyncio.CancelledError):
        await loop.run(ctx)
    assert ctx.status == "failed"
    assert ctx.reason == "cancelled"


# 功能：模型异常导致 llm_error 失败。
# 设计：假 provider 主动抛异常，避免依赖真实网络故障。
async def test_llm_api_error_marks_failed() -> None:
    provider = _MockProvider([], exc=RuntimeError("api error"))
    # 元组解包取出 Loop；下划线变量表示这次不使用返回的总线。
    loop, _ = _make_loop(provider)
    ctx = _ctx()
    await loop.run(ctx)
    assert ctx.status == "failed"
    assert ctx.reason == "llm_error"


# 功能：正常步骤发布开始和结束事件。
# 设计：订阅总线后运行单步任务，观察实际事件。
async def test_step_started_and_finished_events_published() -> None:
    bus = EventBus()
    events = await _events(bus)
    provider = _MockProvider([LlmResponse(stop_reason="end_turn")])
    loop, _ = _make_loop(provider, bus=bus)
    ctx = _ctx()
    await loop.run(ctx)
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "step.started" in types
    assert "step.finished" in types


# 功能：每轮模型调用将步数增加一次。
# 设计：连续两次工具请求后结束，预期总计三步。
async def test_step_counter_increments_across_steps() -> None:
    provider = _MockProvider([
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc()]),
        LlmResponse(stop_reason="tool_use", tool_calls=[_tc()]),
        LlmResponse(stop_reason="end_turn"),
    ])
    registry = ToolRegistry()
    registry.register(_EchoTool())
    loop, _ = _make_loop(provider, registry)
    ctx = _ctx(max_steps=10)
    await loop.run(ctx)
    assert ctx.step == 3
    assert ctx.status == "success"


# 功能：模型文本转换为 assistant 的 text 块。
# 设计：检查角色、块类型和正文，验证消息包装方式。
async def test_assistant_message_blocks_added_to_context() -> None:
    provider = _MockProvider([LlmResponse(stop_reason="end_turn", text="answer")])
    # 元组解包取出 Loop；下划线变量表示这次不使用返回的总线。
    loop, _ = _make_loop(provider)
    ctx = _ctx()
    await loop.run(ctx)
    assistant_msg = ctx.messages[1]
    assert assistant_msg["role"] == "assistant"
    blocks = assistant_msg["content"]
    assert blocks[0]["type"] == "text"  # type: ignore[index]
    assert blocks[0]["text"] == "answer"  # type: ignore[index]
