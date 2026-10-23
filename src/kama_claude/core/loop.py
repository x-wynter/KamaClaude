# ═══ 文件职责：Agent 的逐轮执行逻辑 ═══
# 协作关系：Runner → AgentLoop → Provider / invoke_tool；ExecutionContext 保存每轮更新的状态。
# 阅读路线：先看 run 的六个步骤；每轮对应一次模型请求，工具调用可能有多次。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kama_claude.core.bus.events import StepFinishedEvent, StepStartedEvent
from kama_claude.core.context import ExecutionContext
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.base import LLMProvider
from kama_claude.core.tools.invocation import invoke_tool
from kama_claude.core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from kama_claude.core.permissions.manager import PermissionManager


# ── 函数：_now ──
# 用途：为步骤事件生成时间戳；不用于计算耗时。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 类：AgentLoop ──
# 职责：反复请求模型、执行工具、更新上下文，直到本轮运行终止。
# 字段说明：
#   _provider：请求模型；_registry：查找工具和生成工具说明；_bus：发布进度。
class AgentLoop:
    # 初始化循环所需依赖：LLM provider、工具注册表、事件总线，以及可选的权限管理器和 session ID
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
        *,
        permission_manager: PermissionManager | None = None,
        session_id: str = "",
    ) -> None:
        # 保存协作对象的引用，既不复制它们，也不在构造时执行任务。
        self._provider = provider
        self._registry = registry
        self._bus = bus
        self._permission_manager = permission_manager
        self._session_id = session_id

    # ── 函数：run ──
    # 用途：驱动 plan→act→observe 循环直到上下文终止；CancelledError 向上传播
    # 输入/输出：接收可变 context，直接更新它的消息历史、步数和结束状态。
    async def run(self, context: ExecutionContext) -> None:
        # context 保存可变运行状态；每一步代表一次模型请求，不等于一次工具调用。
        while not context.is_done():
            # 步骤 1：开始新一轮：更新步数并发布开始事件。
            context.step += 1
            # 事件发布会等待订阅者处理；终端输出和文件写入由订阅者完成。
            await self._bus.publish(
                StepStartedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )

            # 步骤 2：请求模型：传入完整历史和工具说明。
            try:
                # await 等待模型完成；依赖接口而非具体供应商，测试可替换实现。
                response = await self._provider.chat(
                    # 这里传完整历史，包括原目标、已有模型回答以及之前的工具结果。
                    messages=context.messages,
                    # schema 描述工具名字和参数格式；它不是函数对象，模型不会直接运行 Python。
                    tool_schemas=self._registry.tool_schemas(),
                    bus=self._bus,
                    run_id=context.run_id,
                    # S3 将轮次传给 provider 包装器，用于把 LLM trace 对应到本步。
                    step=context.step,
                    system=context.system_prompt(
                        "You are a helpful AI assistant. "
                        "Use the available tools to complete the user's goal. "
                        "When the goal is fully achieved, respond with a final answer "
                        "and do not call any more tools."
                    ),
                )
            # 裸 raise 重新抛出当前异常，让 Runner 执行取消收尾。
            except asyncio.CancelledError:
                context.mark_failed("cancelled")
                raise
            # 模型异常终止任务；这条 break 路径不会发布本步的 step.finished。
            except Exception:
                context.mark_failed("llm_error")
                break

            # 步骤 3：把模型文本和工具请求追加到历史。
            # 一条 assistant 消息可含文本和多个 tool_use 块，供下次模型请求使用。
            blocks: list[dict[str, object]] = []
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            # tc 是 ToolCallBlock 实例；点号读取字段，再转成供应商消息格式的字典。
            for tc in response.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )
            # 必须先记录模型提出的工具调用，再记录对应结果，保持对话因果顺序。
            context.add_assistant_message(blocks)

            # 步骤 4：逐个执行工具，并追加每次调用结果。
            # 模型只提出调用请求；实际执行发生在本地 invoke_tool 中，且逐个执行。
            if response.stop_reason == "tool_use":
                # 每次 await 完成后才处理下一个工具；此处没有 create_task 或 gather 并发。
                for tc in response.tool_calls:
                    result = await invoke_tool(
                        self._registry, tc, self._bus, context.run_id,
                        permission_manager=self._permission_manager,
                        session_id=self._session_id,
                    )
                    # 工具失败也写入历史，模型下一步能看到错误并尝试调整。
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)

            # 步骤 5：判断结束原因：模型完成优先于步数上限。
            if response.stop_reason == "end_turn":
                # S3 保存最终回答供 run_and_capture 返回；成功不代表 .tasks 中全部 completed。
                context.result = response.text or ""
                context.mark_success()
            # 只有没有命中 end_turn 时才判断上限，避免完成任务的最后一步被误判失败。
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")

            # 步骤 6：发布本轮结束事件，继续下一轮或退出。
            # 事件发布会等待订阅者处理；终端输出和文件写入由订阅者完成。
            await self._bus.publish(
                StepFinishedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )
