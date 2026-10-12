# S1 唯一内置工具：读取本地文件并将文本返回给模型。

from __future__ import annotations

from pathlib import Path

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 512 * 1024  # 512 KB


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            }
        },
        "required": ["path"],
    }

    # 读取文件内容；超 512KB 截断；禁止 .. 路径遍历
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        path_str = str(params["path"])

        # parts 按当前平台拆分路径；这里只拒绝 ..，并没有拒绝绝对路径或限制根目录。
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        path = Path(path_str)
        # read_bytes 一次读入整个文件；512 KB 限制的是返回内容，不是读取内存量。
        raw = path.read_bytes()  # raises FileNotFoundError if absent
        truncated = len(raw) > _MAX_BYTES
        # 按字节截断后解码；若切断多字节字符，replace 用替代字符避免报错。
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[truncated]"

        return ToolResult(content=text)
