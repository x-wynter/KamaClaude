# ═══ 文件职责：让模型主动保存本会话笔记 ═══
# 协作：Runner 有 session/store/run_id 才注册；Loop → invoke_tool → invoke → Store。
# 阅读顺序：schema → 绑定字段 → invoke；这不是跨会话检索或自动摘要系统。
from __future__ import annotations

from kama_claude.core.session.store import SessionStore
from kama_claude.core.tools.base import BaseTool, ToolResult


class NoteSaveTool(BaseTool):
    # 类字段给模型描述调用方式；模型只提交 JSON 参数，不执行这个 Python 类。
    name = "note_save"
    description = (
        "Save a concise fact or decision to this session's notes. "
        "These notes are visible in future turns of the same session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The durable fact or decision to remember.",
            },
        },
        "required": ["content"],
    }

    # 绑定当前 session 与 run，使工具调用能写入对应 notes.md
    def __init__(self, store: SessionStore, session_id: str, run_id: str) -> None:
        # 三个字段由 Runner 固定，模型不能通过 content 改变笔记所属会话。
        self._store = store
        self._session_id = session_id
        self._run_id = run_id

    # 将非空 content 追加到 session notes.md
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：取参数并拒绝空白；str 是内置转换，strip 去掉首尾空白。
        content = str(params.get("content", "")).strip()
        if not content:
            return ToolResult(
                content="empty content",
                is_error=True,
                error_type="runtime_error",
            )
        # 步骤 2：同步追加 UTF-8 Markdown，再返回工具结果供 Context 回填。
        # 本轮 context.session_notes 是启动快照；下轮 Runner 才重新读完整笔记。
        self._store.append_note(self._session_id, content, self._run_id)
        return ToolResult(content="saved")
