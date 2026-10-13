# ═══ 文件职责：工具执行的公共流程 ═══
# 协作关系：Loop → invoke_tool → 具体工具；成功和失败都转成事件与 ToolResult。
# 阅读路线：先读 invoke_tool 主流程；遇到 _fail 再看统一失败处理，_now 只生成时间。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
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


# ── 函数：_now ──
# 用途：生成事件时间戳；耗时另用单调时钟计算，二者用途不同。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 函数：_fail ──
# 用途：发布 ToolCallFailedEvent 并返回对应 ToolResult
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


# ── 函数：invoke_tool ──
# 用途：校验必填参数、限时调用工具并转换常见执行错误；取消和总线异常仍可能传播
# 输入：注册表、模型提出的调用、总线、任务 ID，以及以秒计的超时时间。
# 输出：ToolResult 供 Loop 追加到历史；开始/成功/失败事件供客户端观察。
async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ToolResult:
    # 步骤 1：开始计时，发布工具开始事件。
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

    # 步骤 2：定义计算本次耗时的局部函数。
    # ── 函数：elapsed ──
    # 用途：闭包读取外层 t0；单调时钟适合计时，不受系统时间调整影响。
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    # 步骤 3：按名称查找工具；不存在则返回失败结果。
    tool = registry.get(tool_call.name)
    # 未知工具在这里提前返回，不会进入实际工具执行逻辑。
    # return await 表示先等待失败事件发布，再把错误结果返回给调用者。
    if tool is None:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", elapsed(),
        )

    # 步骤 4：检查必填参数是否齐全。
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

    # 步骤 5：在超时限制内执行工具，发布成功或失败事件。
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

    # 步骤 6：把超时和普通执行异常转换为工具错误。
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
