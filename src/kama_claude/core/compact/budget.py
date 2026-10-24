# ═══ 文件职责：L1 工具结果的字符预算 ═══
# 主线调用：SessionStore.read_messages → truncate_tool_results；不在每个模型请求前运行。
# 阅读顺序：默认阈值 → user 块筛选 → 浅复制截断；返回新列表，不回写原 thread。
# 只处理字符串 tool_result；未改动的嵌套对象仍可能共享，不能当作深复制。
from __future__ import annotations

from typing import Any

TOOL_RESULT_LIMIT = 8_000
TOOL_RESULT_KEEP = 4_000


# 对消息列表中超长的 tool_result 内容做内存截断，返回处理后的新列表
def truncate_tool_results(
    messages: list[dict[str, Any]],
    limit: int = TOOL_RESULT_LIMIT,
    keep: int = TOOL_RESULT_KEEP,
) -> list[dict[str, Any]]:
    # 超过 limit 才保留 keep 前缀与提示；按字符而非 token 计算。
    result = []
    for msg in messages:
        if msg.get("role") != "user":
            result.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_blocks = []
        for block in content:
            if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                text = block["content"]
                if len(text) > limit:
                    omitted = len(text) - keep
                    block = dict(block)
                    block["content"] = (
                        text[:keep] + f"\n[... {omitted} chars omitted. Full output in run events.]"
                    )
            new_blocks.append(block)
        result.append({**msg, "content": new_blocks})
    return result
