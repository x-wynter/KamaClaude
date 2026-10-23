# ═══ 文件职责：TUI 启动入口 ═══
# 协作关系：kama-tui 或 python -m kama_claude.tui → main → KamaTuiApp.run。
# 阅读路线：声明 --replay 参数 → 读取配置 → 创建界面 → 交给 Textual 运行。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
from pathlib import Path

from kama_claude.core.config import get_config
from kama_claude.tui.app import KamaTuiApp

_DEFAULT_TUI_LOG = "~/.kama/logs/tui.log"


# TUI 文件日志初始化：不写 stderr（避免干扰 Textual 渲染），只写滚动文件
def _setup_logging(level: str) -> None:
    log_path = Path(os.environ.get("KAMA_TUI_LOG_FILE", _DEFAULT_TUI_LOG)).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root.handlers.clear()
    root.addHandler(handler)


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
    _setup_logging(config.logging.level)
    app = KamaTuiApp(config.host, config.port, replay_run_id=args.replay)

    # 步骤 3：交给 Textual 启动界面和事件循环。
    # Textual 的同步 run 内部管理事件循环，此处不再额外套 asyncio.run。
    app.run()


# 作为模块运行时调用 main；被其他代码导入时跳过此分支。
if __name__ == "__main__":
    main()
