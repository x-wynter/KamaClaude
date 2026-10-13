# ═══ 文件职责：创建待办事项的模型工具适配层 ═══
# 协作关系：AgentLoop → invoke_tool → 本工具 → TaskManager → 本 run 的 .tasks 文件。
# 阅读路线：先看 name/input_schema，再看构造依赖，最后读 invoke。
# async 是工具接口的统一约定；这里调用 Manager 时仍同步读写文件。
# schema 告知模型参数格式；invoke_tool 只检查必填键，不进行完整 JSON Schema 校验。

from __future__ import annotations

import json

from kama_claude.core.task.manager import TaskManager
from kama_claude.core.tools.base import BaseTool, ToolResult


class TaskCreateTool(BaseTool):
    # 类字段由 ToolRegistry 读取：name 用于查找，description/schema 提供给模型选择参数。
    name = "task_create"
    description = (
        "Create a new task to track a unit of work. "
        "Use this to break down a complex goal into smaller, trackable steps. "
        "Returns the created task as JSON."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Short title for the task.",
            },
            "description": {
                "type": "string",
                "description": "Optional longer description of what needs to be done.",
            },
            "blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "IDs of tasks that must be completed before this one.",
            },
        },
        "required": ["subject"],
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        # 保存 Runner 注入的同一个 Manager；不会另建目录或新建后台任务。
        self._manager = task_manager

    # 创建任务并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：内置 str/list/int 转换模型给出的参数；列表推导式逐个转换依赖 ID。
        subject = str(params["subject"])
        description = str(params.get("description") or "")
        raw_blocked: list[object] = list(params.get("blocked_by") or [])  # type: ignore[call-overload]
        blocked_by = [int(str(x)) for x in raw_blocked]
        # 步骤 2：项目 create 校验依赖文件存在并保存，返回值是 Task。
        try:
            task = self._manager.create(subject, description, blocked_by)
            # 步骤 3：Task → dict → JSON 字符串；它是工具结果 content，不是 RPC 外壳。
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        # 可预期的业务错误返回 ToolResult；参数转换异常仍由外层 invoke_tool 统一捕获。
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
