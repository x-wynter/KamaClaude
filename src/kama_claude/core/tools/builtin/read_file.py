# ═══ 文件职责：读取本地文件的具体工具 ═══
# 协作关系：模型只提出 read_file 请求；invoke_tool 查到本类后调用 invoke。
# 阅读路线：先看名称和参数说明，再看路径检查、读文件、截断和结果返回。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from pathlib import Path

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 512 * 1024  # 512 KB


# ── 类：ReadFileTool ──
# 职责：实现 read_file 工具，返回本地文件的文本内容。
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

    # ── 函数：invoke ──
    # 用途：读取文件内容；超 512KB 截断；禁止 .. 路径遍历
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：取得 path 参数并检查路径片段。
        # [] 取出 path 的值，缺少键会抛 KeyError；str 将它转为路径字符串。
        # 通过 invoke_tool 调用时，缺键通常已被外层提前识别。
        path_str = str(params["path"])

        # parts 按当前平台拆分路径；这里只拒绝 ..，并没有拒绝绝对路径或限制根目录。
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 步骤 2：读取文件的原始字节。
        # Path 对象表示本地路径；相对路径按进程当前目录解释，不按源码目录解释。
        path = Path(path_str)
        # read_bytes 一次读入整个文件；512 KB 限制的是返回内容，不是读取内存量。
        raw = path.read_bytes()  # raises FileNotFoundError if absent

        # 步骤 3：按上限截取字节并以 UTF-8 解码。
        # raw 是 bytes，因此 len 统计字节数；对解码后的 str 则统计字符数。
        truncated = len(raw) > _MAX_BYTES
        # 按字节截断后解码；若切断多字节字符，replace 用替代字符避免报错。
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")

        # 步骤 4：必要时追加截断提示，再返回统一结果。
        # 追加截断标记，让模型知道这份返回值不是文件的全部内容。
        if truncated:
            text += "\n[truncated]"

        # 未指定 is_error 时使用 dataclass 默认值 False；文件异常由外层调用器转换。
        return ToolResult(content=text)
