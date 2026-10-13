# ═══ 文件职责：按名称保存和查找工具 ═══
# 协作关系：Runner 注册实例；Provider 使用工具说明；调用器按名称取出工具执行。
# 阅读路线：register 是存入实例，get 是取出实例，tool_schemas 是导出说明。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from kama_claude.core.tools.base import BaseTool


# ── 类：ToolRegistry ──
# 职责：保存工具名称到实例的映射，并导出模型可读的工具说明。
# 字段说明：
#   _tools：工具名称到 BaseTool 实例的字典。
class ToolRegistry:
    # ── 初始化：__init__ ──
    # 用途：创建名字到工具实例的映射；同一实例后续可能被多次调用。
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # ── 函数：register ──
    # 用途：注册工具；同名覆盖
    def register(self, tool: BaseTool) -> None:
        # 字典同名赋值会覆盖旧实例，没有自动拒绝重复注册。
        self._tools[tool.name] = tool

    # ── 函数：get ──
    # 用途：按名称查找工具，不存在返回 None
    def get(self, name: str) -> BaseTool | None:
        # dict.get 不存在时返回 None，而 [] 取不存在的键会抛 KeyError。
        return self._tools.get(name)

    # ── 函数：tool_schemas ──
    # 用途：返回所有工具的 Anthropic 格式 schema 列表
    def tool_schemas(self) -> list[dict[str, object]]:
        # 列表推导式将每个实例转为一份工具说明，不执行工具。
        return [
            {
                "name": tool.name,
                "description": tool.description,
                # 这里使用原 schema 字典引用，没有深拷贝；调用方不应随意原地改它。
                "input_schema": tool.input_schema,
            }
            # values 遍历存储的工具实例；列表推导式为每个实例生成上面的描述字典。
            for tool in self._tools.values()
        ]
