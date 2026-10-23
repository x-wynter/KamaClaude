# ═══ 文件职责：工具限流异常接口 ═══
# 协作关系：具体工具可抛 RateLimitedError；invoke_tool 分类后退避重试。
# 推荐阅读：异常类 → invocation 的 except 分支。

from __future__ import annotations


class RateLimitedError(Exception):
    """Raised by a tool when the upstream service is rate-limiting the request."""
