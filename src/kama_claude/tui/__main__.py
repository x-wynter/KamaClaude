# ═══ 文件职责：TUI 启动入口 ═══
# 协作关系：kama-tui 或 python -m kama_claude.tui → main → KamaTuiApp.run。
# 阅读路线：声明 --replay 参数 → 读取配置 → 创建界面 → 交给 Textual 运行。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import argparse

from kama_claude.core.config import get_config
from kama_claude.tui.app import KamaTuiApp


# ── 函数：main ──
# 用途：kama-tui 入口：解析 --replay 参数后启动 TUI 应用
def main() -> None:
    # 步骤 1：声明并解析历史回放参数。
    # 库函数：ArgumentParser 创建参数解析器；add_argument 声明允许的参数，parse_args 读取用户输入。
    parser = argparse.ArgumentParser(prog="kama-tui", description="KamaClaude TUI")
    parser.add_argument(
        "--replay",
        metavar="RUN_ID",
        help="Replay events from a past run on connect",
    )
    args = parser.parse_args()

    # 步骤 2：读取连接配置并创建 TUI 对象。
    # CLI 和 TUI 各自加载地址配置；两者须连接到同一 daemon 的 host/port。
    config = get_config()
    app = KamaTuiApp(config.host, config.port, replay_run_id=args.replay)

    # 步骤 3：交给 Textual 启动界面和事件循环。
    # Textual 的同步 run 内部管理事件循环，此处不再额外套 asyncio.run。
    app.run()


# 作为模块运行时调用 main；被其他代码导入时跳过此分支。
if __name__ == "__main__":
    main()
