# ═══ 文件职责：权限异常接口 ═══
# 协作关系：目前调用器返回 permission_denied ToolResult；此异常仅定义、导出，没有主线抛出点。
# 推荐阅读：先读 invocation 的拒绝返回，再看此接口。

from __future__ import annotations


class PermissionDeniedError(Exception):
    """Raised when a tool call is denied by the permission manager."""
