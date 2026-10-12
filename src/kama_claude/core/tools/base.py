# 工具契约与结果：BaseTool 定义调用接口，ToolResult 统一成功或错误的返回格式。

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    error_type: str | None = None  # "runtime_error" | "timeout" | "schema_error"


# ABC 配合 abstractmethod：子类必须实现 invoke 才能实例化。
class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, object]

    # 执行工具调用，返回结果或错误
    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
