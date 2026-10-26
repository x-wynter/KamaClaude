# 本地集成测试专用入口：真实 Core/TCP/Runner，只替换模型边界，绝不构造 SDK 客户端。
# conftest 在临时工作目录启动此模块；生产 kama-core 入口不使用它。
from __future__ import annotations

from kama_claude.core import app, runner
from kama_claude.core.app import run
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock


class OfflineProvider:
    # 接受与生产构造函数相同的模型参数，但不读取任何凭据。
    def __init__(self, model: str) -> None:
        pass

    # 返回确定性终态，让跨进程测试覆盖开始、完成与事件落盘。
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        if step == 1 and str(messages[-1].get("content", "")).startswith("/orchestrate s7"):
            assert system is not None and "$ARGUMENTS" not in system
            assert "s7" in system
            return LlmResponse(stop_reason="tool_use", tool_calls=[
                ToolCallBlock(id=f"s7-{run_id}", name="spawn_agent", input={
                    "description": "离线子任务", "prompt": "检查子任务", "subagent_type": "planner",
                }),
            ])
        if step == 1 and str(messages[-1].get("content", "")).startswith("s5 permission"):
            return LlmResponse(stop_reason="tool_use", tool_calls=[
                ToolCallBlock(id=f"s5-{run_id}", name="write_file", input={
                    "path": "s5-approved.txt", "content": "中文审批🧭",
                }),
            ])
        return LlmResponse(stop_reason="end_turn", text="offline test complete")


if __name__ == "__main__":
    app.AnthropicProvider = OfflineProvider  # type: ignore[misc,assignment]
    runner.AnthropicProvider = OfflineProvider  # type: ignore[misc,assignment]
    run()
