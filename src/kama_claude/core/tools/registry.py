# 工具注册表：字典保存工具实例，对模型则只暴露名称、说明和参数 schema。

from __future__ import annotations

from kama_claude.core.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # 注册工具；同名覆盖
    def register(self, tool: BaseTool) -> None:
        # 字典同名赋值会覆盖旧实例，没有自动拒绝重复注册。
        self._tools[tool.name] = tool

    # 按名称查找工具，不存在返回 None
    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    # 返回所有工具的 Anthropic 格式 schema 列表
    def tool_schemas(self) -> list[dict[str, object]]:
        # 列表推导式将每个实例转为一份工具说明，不执行工具。
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]
