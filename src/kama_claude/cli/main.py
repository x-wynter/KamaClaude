# 学习入口：把终端参数转换成 Python 属性，再调用版本、ping 或 run 功能。
# ping 走 TCP；run 在当前进程调用 AgentRunner，两者共用配置和日志初始化。

# 推迟类型注解的求值；注解用于说明类型，不会自动检查传入的值。
from __future__ import annotations

# argparse、sys 是 Python 标准库；下面 kama_claude 开头的导入来自本项目。
import argparse
import sys

from kama_claude.cli.commands.ping import cmd_ping
from kama_claude.cli.commands.run import cmd_run
from kama_claude.cli.commands.version import cmd_version
from kama_claude.core.config import get_config
from kama_claude.core.logging_setup import setup_logging


# CLI 主入口：解析命令行参数并分发到对应子命令
def main() -> None:
    # 创建参数解析器；prog 仅设置帮助中的程序名，不负责安装 kama 命令。
    parser = argparse.ArgumentParser(prog="kama", description="KamaClaude CLI")
    # store_true 是布尔开关：出现 --version 时为 True，否则为 False。
    # help 只是帮助文字，真正打印版本需要下面的 cmd_version()。
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    # dest 指定保存子命令名称的属性：kama ping 会得到 args.command == "ping"。
    subparsers = parser.add_subparsers(dest="command")
    # 注册允许使用的子命令；这里只声明名称，并没有绑定处理函数。
    # 默认读取 sys.argv[1:]，返回可用 args.version 等属性访问的 Namespace。
    # -h/--help 会显示帮助并退出；非法参数通常在这里报错并退出。

    subparsers.add_parser("ping", help="Ping the core daemon")

    run_parser = subparsers.add_parser("run", help="Run an agent task")
    run_parser.add_argument("--goal", required=True, help="Goal for the agent to accomplish")

    args = parser.parse_args()

    # 先处理版本开关；return 结束当前函数，避免继续进入下面的命令分支。
    if args.version:
        cmd_version()
        return

    config = get_config()
    setup_logging(config)

    if args.command == "ping":
        cmd_ping(config)
    elif args.command == "run":
        cmd_run(args.goal, config)
    else:
        # 没有提供子命令时 command 为 None，进入此分支。
        parser.print_help()
        # sys.exit 抛出 SystemExit；未被捕获时退出进程，状态码 1 表示失败。
        sys.exit(1)
