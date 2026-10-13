# ═══ 文件职责：生成目录树文本的模型工具 ═══
# 协作关系：invoke_tool → invoke → 内部递归 _walk；结果回到模型与工具事件。
# 阅读路线：先看限制与 schema，再看路径检查，最后展开递归函数。
# 包括隐藏文件；路径只检查 ..，没有实现完整工作区隔离。

from __future__ import annotations

from pathlib import Path

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_DEPTH = 4
_MAX_ENTRIES = 200


# ── 类：ListDirTool ──
# 类字段生成工具说明；_MAX_DEPTH/_MAX_ENTRIES 控制展示规模，不是访问权限边界。
class ListDirTool(BaseTool):
    name = "list_dir"
    description = (
        "List the contents of a directory as a tree. "
        "Path must be relative to the current working directory. "
        "Hidden entries (starting with .) are included. "
        f"Maximum depth is {_MAX_DEPTH}, maximum total entries is {_MAX_ENTRIES}."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the directory (default '.').",
            },
            "max_depth": {
                "type": "integer",
                "description": f"How many levels deep to recurse (default 2, max {_MAX_DEPTH}).",
            },
        },
        "required": [],
    }

    # 以树状格式列出目录内容，深度和条数有上限
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤 1：取得路径与深度；默认深度 2，上限 4，所有递归共用 200 条计数。
        path_str = str(params.get("path") or ".")
        max_depth = min(int(str(params.get("max_depth") or 2)), _MAX_DEPTH)

        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 步骤 2：确认存在且为目录；异常由 invoke_tool 转成模型可读的错误结果。
        root = Path(path_str)
        if not root.exists():
            raise FileNotFoundError(f"no such directory: {path_str}")
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {path_str}")

        # 步骤 3：准备累积列表与闭包计数；下面的 _walk 递归访问子目录。
        lines: list[str] = [str(root) + "/"]
        count = 0

        # 递归收集一层树形文本；prefix 保存父层延伸线，depth 从 1 开始。
        def _walk(directory: Path, depth: int, prefix: str) -> None:
            # nonlocal 允许修改外层局部变量；若不声明，count += 1 会被当作新局部变量。
            nonlocal count
            if depth > max_depth or count >= _MAX_ENTRIES:
                return
            # Path.iterdir 枚举直接子项；sorted 按 (是否文件, 名字) 让目录优先。
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name))
            # 内置 enumerate 同时给出从 0 开始的序号和对象，用于判断是否最后一项。
            for i, entry in enumerate(entries):
                if count >= _MAX_ENTRIES:
                    lines.append(f"{prefix}... (truncated)")
                    return
                connector = "└── " if i == len(entries) - 1 else "├── "
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")
                count += 1
                if entry.is_dir() and depth < max_depth:
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, depth + 1, prefix + extension)

        # 步骤 4：执行递归，再用字符串 join 将每行合成一次工具结果。
        _walk(root, 1, "")
        return ToolResult(content="\n".join(lines))
