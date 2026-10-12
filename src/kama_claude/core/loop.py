# Agent 的执行循环：问模型 → 记录回答 → 调用工具 → 追加结果 → 判断是否结束。

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kama_claude.core.bus.events import StepFinishedEvent, StepStartedEvent
from kama_claude.core.context import ExecutionContext
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.base import LLMProvider
from kama_claude.core.tools.invocation import invoke_tool
from kama_claude.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentLoop:
    # 初始化循环所需的三个依赖：LLM provider、工具注册表、事件总线
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._bus = bus

    # 驱动 plan→act→observe 循环直到上下文终止；CancelledError 向上传播
    async def run(self, context: ExecutionContext) -> None:
        # context 保存可变运行状态；每一步代表一次模型请求，不等于一次工具调用。
        while not context.is_done():
            context.step += 1
            await self._bus.publish(
                StepStartedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )

            # [plan] call LLM — API errors terminate the run
            try:
                # await 等待模型完成；依赖接口而非具体供应商，测试可替换实现。
                response = await self._provider.chat(
                    messages=context.messages,
                    tool_schemas=self._registry.tool_schemas(),
                    bus=self._bus,
                    run_id=context.run_id,
                )
            # 裸 raise 重新抛出当前异常，让 Runner 执行取消收尾。
            except asyncio.CancelledError:
                context.mark_failed("cancelled")
                raise
            # 模型异常终止任务；这条 break 路径不会发布本步的 step.finished。
            except Exception:
                context.mark_failed("llm_error")
                break

            # [observe] append assistant content blocks to context
            # 一条 assistant 消息可含文本和多个 tool_use 块，供下次模型请求使用。
            blocks: list[dict[str, object]] = []
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )
            context.add_assistant_message(blocks)

            # [act] execute each requested tool; errors become tool results so loop continues
            # 模型只提出调用请求；实际执行发生在本地 invoke_tool 中，且逐个执行。
            if response.stop_reason == "tool_use":
                for tc in response.tool_calls:
                    result = await invoke_tool(
                        self._registry, tc, self._bus, context.run_id
                    )
                    # 工具失败也写入历史，模型下一步能看到错误并尝试调整。
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)

            # Termination check — end_turn wins over max_steps if both hit on same step
            # 同一步同时达到步数上限时，end_turn 的成功判断优先。
            if response.stop_reason == "end_turn":
                context.mark_success()
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")

            await self._bus.publish(
                StepFinishedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )
