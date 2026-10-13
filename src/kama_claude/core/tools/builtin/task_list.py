# ═══ 文件职责：返回全量清单摘要的模型工具适配层 ═══
# 协作关系：AgentLoop → invoke_tool → 本工具 → TaskManager → 本 run 的 .tasks 文件。
# 阅读路线：先看 name/input_schema，再看构造依赖，最后读 invoke。
# async 是工具接口的统一约定；这里调用 Manager 时仍同步读写文件。
# schema 告知模型参数格式；invoke_tool 只检查必填键，不进行完整 JSON Schema 校验。

from __future__ import annotations

from kama_claude.core.task.manager import TaskManager
from kama_claude.core.tools.base import BaseTool, ToolResult


class TaskListTool(BaseTool):
    # 类字段由 ToolRegistry 读取：name 用于查找，description/schema 提供给模型选择参数。
    name = "task_list"
    description = (
        "List all tasks with their current status and blocking dependencies. "
        "Use this to check what work remains and what can be started next."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        # 保存 Runner 注入的同一个 Manager；不会另建目录或新建后台任务。
        self._manager = task_manager

    # 返回格式化的任务列表摘要
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 项目 format_list 返回 [ ]/[>]/[x] 标记的文本，供模型判断下一步；不自动执行它。
        return ToolResult(content=self._manager.format_list())
