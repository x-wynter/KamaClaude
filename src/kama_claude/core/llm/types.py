# ═══ 文件职责：模型返回值的数据结构 ═══
# 协作关系：Provider 将 SDK 返回值转换为这些对象；Loop 读取文本、工具调用和结束原因。
# 阅读路线：UsageStats 保存用量，ToolCallBlock 保存调用请求，LlmResponse 汇总一次响应。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from dataclasses import dataclass, field


# ── 类：UsageStats ──
# 职责：记录一次模型请求的 token 用量。
# dataclass 自动生成初始化等方法；普通类型提示不执行 Pydantic 式校验。
@dataclass
class UsageStats:
    # 记录模型输入和输出 token 数；不是字符数，也不是 llm.token 事件条数。
    input_tokens: int
    output_tokens: int
    # 缺省为 0，表示调用方未提供缓存计数；不会在这里计算费用。
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # Provider 的占用估算，供 Loop 触发阈值与 TUI 展示。
    context_pct: float = 0.0


# ── 类：ToolCallBlock ──
# 职责：保存模型提出的一次工具调用，不会自行执行工具。
# id 用来把 tool_result 对应回 tool_use；name 用来从注册表找工具。
@dataclass
class ToolCallBlock:
    # 字段：模型分配的调用编号，用于关联工具返回值。
    id: str
    # 字段：工具注册名称。
    name: str
    # input 是字段名，不是调用 Python 的 input()；保存模型给出的工具参数。
    input: dict[str, object]


# ── 类：LlmResponse ──
# 职责：汇总一次模型调用的文本、工具请求、用量和结束原因。
@dataclass
class LlmResponse:
    # Loop 主要识别 end_turn 和 tool_use；str 标注本身不限制可用字符串。
    stop_reason: str  # "end_turn" | "tool_use"
    # 每份响应独立创建工具调用列表，避免共享可变默认值。
    tool_calls: list[ToolCallBlock] = field(default_factory=list)
    # 字段：本次模型响应的完整文本。
    text: str = ""
    # | None 表示可以没有用量对象，测试中的简化响应通常省略此字段。
    usage: UsageStats | None = None
    # thinking blocks from extended thinking — must be preserved verbatim in conversation history
    # 保存适配器返回的 thinking/signature，Loop 用于后续请求与会话历史。
    thinking_blocks: list[dict[str, object]] = field(default_factory=list)
