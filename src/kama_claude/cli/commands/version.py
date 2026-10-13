# ═══ 文件职责：显示项目版本 ═══
# 协作关系：CLI 的 --version 分支调用 cmd_version，从顶层包读取版本号。
# 阅读路线：本文件只有一个输出函数，不涉及网络或 Agent 执行。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
import kama_claude


# ── 函数：cmd_version ──
# 用途：打印当前 kama_claude 包的版本号
def cmd_version() -> None:
    print(kama_claude.__version__)
