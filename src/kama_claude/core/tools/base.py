# ═══ 文件职责：工具接口与统一返回值 ═══
# 协作关系：具体工具继承 BaseTool；调用器和 Loop 通过 ToolResult 读取结果。
# 阅读路线：先看返回值字段，再看工具元信息和必须实现的 invoke 接口。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel


# ── 类：ToolResult ──
# 职责：统一表示工具成功输出或失败说明。
@dataclass
class ToolResult:
    # content 是工具输出文本；is_error 区分业务成功和失败，error_type 描述失败类别。
    content: str
    is_error: bool = False
    # "runtime_error" | "timeout" | "schema_error" | "permission_denied" | "rate_limited"
    error_type: str | None = None


# ── 类：BaseTool ──
# 职责：约定工具元信息和 invoke 方法，具体工具负责实现。
# ABC 配合 abstractmethod：子类必须实现 invoke 才能实例化。
class BaseTool(ABC):
    # 子类通常用类属性填写 name、description、input_schema；注册表读取这些属性。
    name: str
    description: str
    input_schema: dict[str, object]
    # ClassVar 表示类级契约；None 保留旧工具自己检查参数的路径。
    params_model: ClassVar[type[BaseModel] | None] = None

    # ── 函数：invoke ──
    # 用途：执行工具调用，返回结果或错误
    # 装饰器标记待实现方法；只继承 BaseTool 而不实现 invoke 的类不能实例化。
    # 这里的 ... 仅表示省略实现，真正执行逻辑位于 ReadFileTool 等子类。
    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
