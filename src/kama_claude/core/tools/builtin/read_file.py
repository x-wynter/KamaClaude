# S1 唯一内置工具：读取本地文件并将文本返回给模型。

from __future__ import annotations

from pathlib import Path

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 512 * 1024  # 512 KB


class ReadFileTool(BaseTool):
    # 工具名称同时用于注册表查找和模型请求匹配；说明文字帮助模型选择工具。
    name = "read_file"
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    # 这是描述参数结构的普通 Python 字典，不是自动执行校验的 Pydantic 模型。
    # properties 声明 path 字段，required 表示该键必须存在。
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
        # [] 取出 path 的值，缺少键会抛 KeyError；str 将它转为路径字符串。
        # 通过 invoke_tool 调用时，缺键通常已被外层提前识别。
        path_str = str(params["path"])

        # parts 按当前平台拆分路径；这里只拒绝 ..，并没有拒绝绝对路径或限制根目录。
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # Path 对象表示本地路径；相对路径按进程当前目录解释，不按源码目录解释。
        path = Path(path_str)
        # read_bytes 一次读入整个文件；512 KB 限制的是返回内容，不是读取内存量。
        raw = path.read_bytes()  # raises FileNotFoundError if absent
        # raw 是 bytes，因此 len 统计字节数；对解码后的 str 则统计字符数。
        truncated = len(raw) > _MAX_BYTES
        # 按字节截断后解码；若切断多字节字符，replace 用替代字符避免报错。
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        # 追加截断标记，让模型知道这份返回值不是文件的全部内容。
        if truncated:
            text += "\n[truncated]"

        # 未指定 is_error 时使用 dataclass 默认值 False；文件异常由外层调用器转换。
        return ToolResult(content=text)
