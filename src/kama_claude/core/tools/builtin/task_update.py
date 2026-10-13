# ═══ 文件职责：修改状态与依赖的模型工具适配层 ═══
# 协作关系：AgentLoop → invoke_tool → 本工具 → TaskManager → 本 run 的 .tasks 文件。
# 阅读路线：先看 name/input_schema，再看构造依赖，最后读 invoke。
# async 是工具接口的统一约定；这里调用 Manager 时仍同步读写文件。
# schema 告知模型参数格式；invoke_tool 只检查必填键，不进行完整 JSON Schema 校验。

from __future__ import annotations

import json

from kama_claude.core.task.manager import TaskManager
from kama_claude.core.task.model import TaskStatus
from kama_claude.core.tools.base import BaseTool, ToolResult


class TaskUpdateTool(BaseTool):
    # 类字段由 ToolRegistry 读取：name 用于查找，description/schema 提供给模型选择参数。
    name = "task_update"
    description = (
        "Update a task's status or dependency list. "
        "Set status to 'in_progress' when starting work on a task, "
        "'completed' when finished (automatically clears it from other tasks' blocked_by). "
        "Returns the updated task as JSON."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status for the task.",
            },
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs to add to blocked_by.",
            },
            "remove_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs to remove from blocked_by.",
            },
        },
        "required": ["task_id"],
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        # 保存 Runner 注入的同一个 Manager；不会另建目录或新建后台任务。
        self._manager = task_manager

    # 更新任务并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：提取 ID、可选状态和两组依赖；None 表示不更新该字段。
        task_id = int(str(params["task_id"]))
        status: TaskStatus | None = params.get("status")  # type: ignore[assignment]
        raw_add: list[object] = list(params.get("add_blocked_by") or [])  # type: ignore[call-overload]
        raw_rem: list[object] = list(params.get("remove_blocked_by") or [])  # type: ignore[call-overload]
        add_blocked = [int(str(x)) for x in raw_add]
        remove_blocked = [int(str(x)) for x in raw_rem]
        # 步骤 2：Manager 完成校验/写盘；completed 会从其他任务依赖中删掉当前 ID。
        # 这是依赖清理，不会自动启动那些任务；状态也不要求按固定顺序迁移。
        try:
            task = self._manager.update(
                task_id,
                status=status,
                add_blocked_by=add_blocked or None,
                remove_blocked_by=remove_blocked or None,
            )
            # 步骤 3：返回修改后的完整记录，让下一轮模型知道实际存储状态。
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 步骤 3：返回修改后的完整记录，让下一轮模型知道实际存储状态。
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
