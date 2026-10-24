# ═══ 文件职责：加载 UTF-8 的全局或项目记忆文本 ═══
# Runner 每次 run 调用两次；Session Notes 仍由 SessionStore 读取。
# 阅读顺序：展开用户目录 → 缺失判断 → UTF-8 读取；不是检索器或文件监听器。
from __future__ import annotations

from pathlib import Path


# 读取指定路径的 context.md，路径不存在或内容为空时返回空字符串
def load_context_file(path: Path) -> str:
    p = path.expanduser()
    if not p.exists():
        return ""
    # 权限/编码异常向上传播；不会把不可读文件伪装成空记忆。
    return p.read_text(encoding="utf-8").strip()
