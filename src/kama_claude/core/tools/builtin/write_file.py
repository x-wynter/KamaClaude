# ═══ 文件职责：创建或覆盖文本文件的模型工具 ═══
# 协作关系：invoke_tool 统一捕获异常；本工具负责尺寸检查、目录创建和 UTF-8 写入。
# 阅读路线：工具契约 → invoke 三步 → ToolResult；与 read_file 对照阅读。
# async 函数内部没有 await，因此文件写入在事件循环线程同步执行。

from __future__ import annotations

from pathlib import Path

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


# ── 类：WriteFileTool ──
# 类字段提供工具契约；磁盘修改发生在 invoke，返回 ToolResult 供 Loop 加入历史。
class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist, or overwriting it if it does. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write.",
            },
        },
        "required": ["path", "content"],
    }

    # 写入文件内容；超 1MB 拒绝；禁止 .. 路径遍历；自动创建父目录
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：将参数转为字符串；Path.parts 按路径组件检查 ..，不是检查子字符串。
        path_str = str(params["path"])
        content = str(params["content"])

        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 步骤 2：str.encode 返回字节串；按 UTF-8 字节长度限制 1 MiB，而非字符数。
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            return ToolResult(
                content=f"content too large: {len(encoded)} bytes (limit 1 MB)",
                is_error=True,
                error_type="runtime_error",
            )

        # 步骤 3：标准库 Path.mkdir 补齐父目录，write_text 以 UTF-8 覆盖写入并关闭文件。
        # 描述要求相对路径，但实现仅拒绝 ..；没有拒绝绝对路径或符号链接逃逸。
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        return ToolResult(content=f"wrote {len(encoded)} bytes to {path_str}")
