# ═══ 文件职责：给模型接口添加调用前后 trace ═══
# 协作关系：AgentLoop → TracingProvider → AnthropicProvider；记录异步交给 TraceWriter。
# 阅读路线：先看构造函数依赖，再顺着 chat 的请求记录、委托、响应记录阅读。
# 已实现耗时和载荷记录；未实现重试、费用预算、模型路由。

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from typing import Any

from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.base import LLMProvider
from kama_claude.core.llm.types import LlmResponse
from kama_claude.core.trace.record import TraceRecord
from kama_claude.core.trace.writer import TraceWriter


# 用标准库 datetime 创建 UTC 时间；耗时使用下方 monotonic 差值。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 类：TracingProvider ──
# 职责：实现同一 chat 接口并委托 _inner；这是包装/装饰模式，不替代真实模型。
# _trace 指向 daemon 共享写入器；_include_payload 只控制 LLM 记录的详细程度。
class TracingProvider:
    # 包裹真实 LLMProvider，在每次 chat() 调用前后向 TraceWriter 写入完整 API I/O 记录
    def __init__(
        self,
        inner: LLMProvider,
        trace: TraceWriter,
        *,
        include_payload: bool = True,
    ) -> None:
        self._inner = inner
        self._trace = trace
        self._include_payload = include_payload

    # 记录 CORE→LLM 请求，调用真实 provider，记录 LLM→CORE 响应（含延迟）
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
    ) -> LlmResponse:
        # 步骤 1：选择请求载荷或计数摘要；这里只捕获项目接口参数，并非 SDK 原始 HTTP。
        call_data: dict[str, Any]
        if self._include_payload:
            call_data = {"messages": messages, "tool_schemas": tool_schemas}
        else:
            call_data = {
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE→LLM",
                layer="llm",
                kind="api_call",
                run_id=run_id,
                step=step,
                data=call_data,
            )
        )

        # 步骤 2：标准库 monotonic 计时，await 内部 provider 后取得完整 LlmResponse。
        # inner 异常仅记录类型和 HTTP 状态码，再原样抛出；不存异常正文以免泄露凭据。
        t0 = time.monotonic()
        try:
            result = await self._inner.chat(messages, tool_schemas, bus, run_id, step=step)
        except Exception as exc:
            error_data: dict[str, Any] = {
                "error_type": type(exc).__name__,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                error_data["status_code"] = status_code
            self._trace.emit(TraceRecord(
                ts=_now(), direction="LLM→CORE", layer="llm", kind="api_error",
                run_id=run_id, step=step, data=error_data,
            ))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)

        # 步骤 3：dataclasses.asdict 将工具调用/用量对象转为可 JSON 序列化的字典。
        resp_data: dict[str, Any]
        if self._include_payload:
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="LLM→CORE",
                layer="llm",
                kind="api_response",
                run_id=run_id,
                step=step,
                data=resp_data,
            )
        )

        # 步骤 4：保持内层响应对象原样返回，Loop 无需知道是否启用了 trace。
        return result
