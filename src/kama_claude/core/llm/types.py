# 内部模型返回值：把供应商响应转换成项目自己的数据对象，供执行循环使用。

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
# dataclass 自动生成初始化等方法；普通类型提示不执行 Pydantic 式校验。
class UsageStats:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
# id 用来把 tool_result 对应回 tool_use；name 用来从注册表找工具。
class ToolCallBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class LlmResponse:
    stop_reason: str  # "end_turn" | "tool_use"
    # 每份响应独立创建工具调用列表，避免共享可变默认值。
    tool_calls: list[ToolCallBlock] = field(default_factory=list)
    text: str = ""
    usage: UsageStats | None = None
