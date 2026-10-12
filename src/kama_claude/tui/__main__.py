from __future__ import annotations

import argparse

from kama_claude.core.config import get_config
from kama_claude.tui.app import KamaTuiApp


# kama-tui 入口：解析 --replay 参数后启动 TUI 应用
def main() -> None:
    parser = argparse.ArgumentParser(prog="kama-tui", description="KamaClaude TUI")
    parser.add_argument(
        "--replay",
        metavar="RUN_ID",
        help="Replay events from a past run on connect",
    )
    args = parser.parse_args()

    # CLI 和 TUI 各自加载地址配置；两者须连接到同一 daemon 的 host/port。
    config = get_config()
    app = KamaTuiApp(config.host, config.port, replay_run_id=args.replay)
    # Textual 的同步 run 内部管理事件循环，此处不再额外套 asyncio.run。
    app.run()


# 作为模块运行时调用 main；被其他代码导入时跳过此分支。
if __name__ == "__main__":
    main()
