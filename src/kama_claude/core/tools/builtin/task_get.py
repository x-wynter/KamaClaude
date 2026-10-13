# ═══ 文件职责：按 ID 读取详情的模型工具适配层 ═══
# 协作关系：AgentLoop → invoke_tool → 本工具 → TaskManager → 本 run 的 .tasks 文件。
# 阅读路线：先看 name/input_schema，再看构造依赖，最后读 invoke。
# async 是工具接口的统一约定；这里调用 Manager 时仍同步读写文件。
# schema 告知模型参数格式；invoke_tool 只检查必填键，不进行完整 JSON Schema 校验。

from __future__ import annotations

import json

from kama_claude.core.task.manager import TaskManager
from kama_claude.core.tools.base import BaseTool, ToolResult


class TaskGetTool(BaseTool):
    # 类字段由 ToolRegistry 读取：name 用于查找，description/schema 提供给模型选择参数。
    name = "task_get"
    description = "Get full details of a task by its integer ID. Returns the task as JSON."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to retrieve.",
            },
        },
        "required": ["task_id"],
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        # 保存 Runner 注入的同一个 Manager；不会另建目录或新建后台任务。
        self._manager = task_manager

    # 获取任务详情并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 内置 int 转为本地整数编号；不存在由 Manager 抛 ValueError，再转为工具错误。
        task_id = int(str(params["task_id"]))
        try:
            task = self._manager.get(task_id)
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
