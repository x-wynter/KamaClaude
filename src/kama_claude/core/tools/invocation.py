# ═══ 文件职责：工具执行的公共流程 ═══
# 协作关系：Loop → invoke_tool → 具体工具；成功和失败都转成事件与 ToolResult。
# 阅读路线：先读 invoke_tool 主流程；遇到 _fail 再看统一失败处理，_now 只生成时间。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from kama_claude.core.bus.events import (
    PermissionDeniedEvent,
    PermissionGrantedEvent,
    PermissionRequestedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import ToolCallBlock
from kama_claude.core.tools.base import ToolResult
from kama_claude.core.tools.errors import RateLimitedError
from kama_claude.core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from kama_claude.core.permissions.manager import PermissionManager

_DEFAULT_TIMEOUT: float = 120.0
_MAX_RETRIES: int = 2
_RETRY_BASE_S: float = 2.0  # backoff base; tests can monkeypatch to 0
_RETRYABLE: frozenset[str] = frozenset({"runtime_error", "rate_limited"})


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
    error_class: str,
    error_message: str,
    elapsed_ms: int,
    *,
    attempt: int = 1,
) -> ToolResult:
    await bus.publish(
        ToolCallFailedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            error_class=error_class,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            attempt=attempt,
            ts=_now(),
        )
    )
    return ToolResult(content=error_message, is_error=True, error_type=error_class)


# 校验、审批、限时执行并退避重试；业务失败返回结果，取消和广播异常仍可传播。
async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
    *,
    permission_manager: PermissionManager | None = None,
    session_id: str = "",
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

    # 步骤4：Pydantic 校验已迁移的工具参数；尚无 params_model 的任务工具跳过此项。
    # 此处丢弃校验结果；内置工具 invoke 会再次解析，并使用转换后的字段。
    if tool.params_model is not None:
        try:
            tool.params_model.model_validate(dict(tool_call.input))
        except ValidationError as exc:
            return await _fail(
                bus, run_id, tool_call,
                "schema_error", str(exc), elapsed(),
            )

    # 步骤5：审批在实际 invoke 之前，只询问一次；重试不会再次弹审批。
    # 没传 Manager 的直接 Python 调用不启用审批，Core 的工厂始终传入。
    if permission_manager is not None:
        # 适配回调把普通 dict 加上 run_id，转换为总线事件。
        async def _emit_permission(raw: dict[str, Any]) -> None:
            await bus.publish(PermissionRequestedEvent(**raw, run_id=run_id))

        allowed, decision = await permission_manager.check_and_wait(
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            session_id=session_id,
            event_emitter=_emit_permission,
        )
        if allowed:
            if decision not in ("auto_allow",):
                await bus.publish(
                    PermissionGrantedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
        else:
            if decision != "auto_deny":
                await bus.publish(
                    PermissionDeniedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
            return await _fail(
                bus, run_id, tool_call,
                "permission_denied",
                "Permission denied by user. You may not execute this command. "
                "Try an alternative approach or ask the user what to do.",
                elapsed(),
            )

    # 步骤6：最多三次执行；运行错误/限流退避 2、4 秒，其他分类立即返回。
    # 每次执行有独立 timeout，审批和退避不在该时间内；耗时则从最初累计。
    for attempt in range(1, _MAX_RETRIES + 2):
        error_class: str | None = None
        error_message: str | None = None

        try:
            result = await asyncio.wait_for(
                tool.invoke(dict(tool_call.input)), timeout=timeout
            )
            ms = elapsed()

            if result.is_error:
                error_class = result.error_type or "runtime_error"
                error_message = result.content
            else:
                await bus.publish(
                    ToolCallFinishedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        tool_name=tool_call.name,
                        elapsed_ms=ms,
                        output=result.content,
                        ts=_now(),
                    )
                )
                return result

        except RateLimitedError as exc:
            error_class = "rate_limited"
            error_message = str(exc)
        except TimeoutError:
            return await _fail(
                bus, run_id, tool_call,
                "timeout", f"tool timed out after {timeout}s", elapsed(),
                attempt=attempt,
            )
        except Exception as exc:
            error_class = "runtime_error"
            error_message = str(exc)

        assert error_class is not None and error_message is not None
        ms = elapsed()

        if error_class in _RETRYABLE and attempt <= _MAX_RETRIES:
            await bus.publish(
                ToolCallFailedEvent(
                    run_id=run_id,
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    error_class=error_class,
                    error_message=error_message,
                    elapsed_ms=ms,
                    attempt=attempt,
                    ts=_now(),
                )
            )
            # sleep 可以被取消；重试可能重复已经部分完成的副作用。
            await asyncio.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            continue

        return await _fail(
            bus, run_id, tool_call,
            error_class, error_message, ms,
            attempt=attempt,
        )

    # unreachable, but keeps mypy happy
    return ToolResult(content="internal error", is_error=True, error_type="runtime_error")
