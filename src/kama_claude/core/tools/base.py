# 工具契约与结果：BaseTool 定义调用接口，ToolResult 统一成功或错误的返回格式。

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    # content 是工具输出文本；is_error 区分业务成功和失败，error_type 描述失败类别。
    content: str
    is_error: bool = False
    error_type: str | None = None  # "runtime_error" | "timeout" | "schema_error"


# ABC 配合 abstractmethod：子类必须实现 invoke 才能实例化。
class BaseTool(ABC):
    # 子类通常用类属性填写 name、description、input_schema；注册表读取这些属性。
    name: str
    description: str
    input_schema: dict[str, object]

    # 执行工具调用，返回结果或错误
    # 装饰器标记待实现方法；只继承 BaseTool 而不实现 invoke 的类不能实例化。
    # 这里的 ... 仅表示省略实现，真正执行逻辑位于 ReadFileTool 等子类。
    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
