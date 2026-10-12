# 内部模型返回值：把供应商响应转换成项目自己的数据对象，供执行循环使用。

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
# dataclass 自动生成初始化等方法；普通类型提示不执行 Pydantic 式校验。
class UsageStats:
    # 记录模型输入和输出 token 数；不是字符数，也不是 llm.token 事件条数。
    input_tokens: int
    output_tokens: int
    # 缺省为 0，表示调用方未提供缓存计数；不会在这里计算费用。
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
# id 用来把 tool_result 对应回 tool_use；name 用来从注册表找工具。
class ToolCallBlock:
    id: str
    name: str
    # input 是字段名，不是调用 Python 的 input()；保存模型给出的工具参数。
    input: dict[str, object]


@dataclass
class LlmResponse:
    # Loop 主要识别 end_turn 和 tool_use；str 标注本身不限制可用字符串。
    stop_reason: str  # "end_turn" | "tool_use"
    # 每份响应独立创建工具调用列表，避免共享可变默认值。
    tool_calls: list[ToolCallBlock] = field(default_factory=list)
    text: str = ""
    # | None 表示可以没有用量对象，测试中的简化响应通常省略此字段。
    usage: UsageStats | None = None
