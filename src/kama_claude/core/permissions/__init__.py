# ═══ 文件职责：权限包公共入口 ═══
# 协作关系：Core/Runner 导入 Manager；policy 提供规则，storage 保存 always 决策。
# 推荐阅读：policy → manager → storage → tools/invocation。

from kama_claude.core.permissions.errors import PermissionDeniedError
from kama_claude.core.permissions.manager import PermissionManager
from kama_claude.core.permissions.policy import PermissionDecision, ToolPolicy
from kama_claude.core.permissions.storage import load_policy_file, save_policy_file

__all__ = [
    "PermissionDecision",
    "PermissionDeniedError",
    "PermissionManager",
    "ToolPolicy",
    "load_policy_file",
    "save_policy_file",
]
