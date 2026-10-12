# 注册表测试：检查名称查找、覆盖和 schema 导出，不执行实际工具。

from __future__ import annotations

from kama_claude.core.tools.base import BaseTool, ToolResult
from kama_claude.core.tools.registry import ToolRegistry


class _FakeTool(BaseTool):
    name = "fake"
    description = "A fake tool"
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        return ToolResult(content="ok")


# 功能：查询返回注册时的工具实例。
# 设计：is 检查对象身份，不仅比较属性值。
def test_register_and_get() -> None:
    registry = ToolRegistry()
    tool = _FakeTool()
    registry.register(tool)
    # is 比较是否同一个对象；== 通常比较值，不能替代此处的身份检查。
    assert registry.get("fake") is tool


# 功能：找不到名称时返回 None。
# 设计：空注册表覆盖缺省查找行为。
def test_get_unknown_returns_none() -> None:
    assert ToolRegistry().get("missing") is None


# 功能：导出的工具描述包含名称、说明和参数结构。
# 设计：检查模型请求所需字段，不调用工具。
def test_tool_schemas_contains_name_description_input_schema() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool())
    schemas = registry.tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "fake"
    assert schemas[0]["description"] == "A fake tool"
    assert "input_schema" in schemas[0]


# 功能：多个工具均进入 schema 列表。
# 设计：集合推导式比较名字集合，不把排序作为契约。
def test_multiple_tools_all_appear_in_schemas() -> None:
    class _AnotherTool(BaseTool):
        name = "another"
        description = "Another"
        input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

        async def invoke(self, params: dict[str, object]) -> ToolResult:
            return ToolResult(content="")

    registry = ToolRegistry()
    registry.register(_FakeTool())
    registry.register(_AnotherTool())
    # 集合推导式只关心名称是否齐全，不关心注册顺序。
    names = {s["name"] for s in registry.tool_schemas()}
    assert names == {"fake", "another"}


# 功能：同名注册用后来的工具覆盖旧工具。
# 设计：两个类使用相同 name、不同 description，以便辨别最终实例。
def test_register_same_name_overwrites() -> None:
    class _Updated(BaseTool):
        name = "fake"
        description = "updated"
        input_schema: dict[str, object] = {}

        async def invoke(self, params: dict[str, object]) -> ToolResult:
            return ToolResult(content="")

    registry = ToolRegistry()
    registry.register(_FakeTool())
    registry.register(_Updated())
    found = registry.get("fake")
    assert found is not None
    assert found.description == "updated"
