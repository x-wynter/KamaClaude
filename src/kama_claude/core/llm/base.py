# ═══ 文件职责：模型提供者的接口约定 ═══
# 协作关系：AgentLoop 依赖此接口；AnthropicProvider 或测试替身提供实际实现。
# 阅读路线：这里只说明 chat 接收什么、返回什么；具体请求过程看 provider.py。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from typing import Protocol

from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse


# ── 类：LLMProvider ──
# 职责：用 Protocol 声明兼容的 chat 方法，供类型检查和依赖替换使用。
# 结构化类型检查关注是否提供兼容的 chat；末尾 ... 表示这里只声明接口。
class LLMProvider(Protocol):
    # ── 函数：chat ──
    # 用途：流式调用 LLM 并发布进度事件，返回完整响应
    # 输入为历史消息、工具说明、总线和任务 ID；await 后得到统一的 LlmResponse。
    # 这是类型契约，函数体的 ... 不是请求模型的实际实现。
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        # S3 新增的仅限关键字参数；Trace 包装器记录轮次，具体 SDK 实现仍按原方式请求。
        step: int = 0,
        # S4：Loop 传入包含会话笔记的系统指令；Protocol 只约定接口，不请求网络。
        system: str | None = None,
    ) -> LlmResponse: ...
