# 模型接口：Protocol 用方法形状约定依赖，实际 provider 不必显式继承它。

from __future__ import annotations

from typing import Protocol

from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse


# 结构化类型检查关注是否提供兼容的 chat；末尾 ... 表示这里只声明接口。
class LLMProvider(Protocol):
    # 流式调用 LLM 并发布进度事件，返回完整响应
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
    ) -> LlmResponse: ...
