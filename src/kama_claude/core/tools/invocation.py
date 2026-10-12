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


# 生成事件时间戳；耗时另用单调时钟计算，二者用途不同。
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
    # 同一次失败有两条输出路径：事件用于观察，返回值供 Loop 放入模型历史。
    return ToolResult(content=error_message, is_error=True, error_type=error_type)


# 校验必填参数、限时调用工具并转换常见执行错误；取消和总线异常仍可能传播
async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ToolResult:
    # 记录本次调用的开始时刻；差值单位是秒，elapsed 再转换成毫秒整数。
    t0 = time.monotonic()

    await bus.publish(
        ToolCallStartedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            # 复制外层参数字典用于开始事件；嵌套对象仍可能共享引用。
            params=dict(tool_call.input),
            ts=_now(),
        )
    )

    # 闭包读取外层 t0；单调时钟适合计时，不受系统时间调整影响。
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    tool = registry.get(tool_call.name)
    # 未知工具在这里提前返回，不会进入实际工具执行逻辑。
    # return await 表示先等待失败事件发布，再把错误结果返回给调用者。
    if tool is None:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", elapsed(),
        )

    # cast 只告知类型检查器，不转换数据；这里仅检查必填键，不做完整 schema 校验。
    required: list[str] = cast(list[str], tool.input_schema.get("required", []))
    # 列表推导式收集缺失的参数名，空列表在 if 中为 False。
    missing = [p for p in required if p not in tool_call.input]
    # 非空列表表示至少缺少一个必填键；join 把键名拼成便于阅读的错误信息。
    if missing:
        return await _fail(
            bus, run_id, tool_call,
            "schema_error", f"missing required parameters: {', '.join(missing)}", elapsed(),
        )

    try:
        # 超时会取消被等待协程并抛 TimeoutError；外层将它转换为工具错误。
        result = await asyncio.wait_for(tool.invoke(dict(tool_call.input)), timeout=timeout)
        ms = elapsed()
        # 工具可以主动返回错误，也可以抛异常；这里处理前者，下方 except 处理后者。
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
        # 成功时保留工具原始返回对象，Loop 会读取其中的 content。
        return result
    # wait_for 的取消依赖协程配合；同步阻塞 I/O 不能被此超时机制立即打断。
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
