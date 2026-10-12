# 工具调用统一入口：开始事件 → 参数检查 → 限时执行 → 成功或失败事件。

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import cast

from kama_claude.core.bus.events import (
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import ToolCallBlock
from kama_claude.core.tools.base import ToolResult
from kama_claude.core.tools.registry import ToolRegistry

_DEFAULT_TIMEOUT: float = 10.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


# 发布 ToolCallFailedEvent 并返回对应 ToolResult
async def _fail(
    bus: EventBus,
    run_id: str,
    tool_call: ToolCallBlock,
    error_type: str,
    error_message: str,
    elapsed_ms: int,
) -> ToolResult:
    await bus.publish(
        ToolCallFailedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            error_type=error_type,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            ts=_now(),
        )
    )
    return ToolResult(content=error_message, is_error=True, error_type=error_type)


# 校验参数、限时调用工具、发布进度事件，返回 ToolResult（不抛异常）
async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ToolResult:
    t0 = time.monotonic()

    await bus.publish(
        ToolCallStartedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            ts=_now(),
        )
    )

    # 闭包读取外层 t0；单调时钟适合计时，不受系统时间调整影响。
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    tool = registry.get(tool_call.name)
    if tool is None:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", elapsed(),
        )

    # cast 只告知类型检查器，不转换数据；这里仅检查必填键，不做完整 schema 校验。
    required: list[str] = cast(list[str], tool.input_schema.get("required", []))
    # 列表推导式收集缺失的参数名，空列表在 if 中为 False。
    missing = [p for p in required if p not in tool_call.input]
    if missing:
        return await _fail(
            bus, run_id, tool_call,
            "schema_error", f"missing required parameters: {', '.join(missing)}", elapsed(),
        )

    try:
        # 超时会取消被等待协程并抛 TimeoutError；外层将它转换为工具错误。
        result = await asyncio.wait_for(tool.invoke(dict(tool_call.input)), timeout=timeout)
        ms = elapsed()
        if result.is_error:
            return await _fail(
                bus, run_id, tool_call,
                result.error_type or "runtime_error", result.content, ms,
            )
        await bus.publish(
            ToolCallFinishedEvent(
                run_id=run_id,
                tool_use_id=tool_call.id,
                tool_name=tool_call.name,
                elapsed_ms=ms,
                ts=_now(),
            )
        )
        return result
    except TimeoutError:
        return await _fail(
            bus, run_id, tool_call,
            "timeout", f"tool timed out after {timeout}s", elapsed(),
        )
    # 不捕获 CancelledError（属于 BaseException），任务取消仍会向上传播。
    except Exception as exc:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", str(exc), elapsed(),
        )
