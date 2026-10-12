# python -m kama_claude.cli 的入口；导入参数解析函数，然后调用。
# 安装后的 kama 命令由 pyproject.toml 直接指向 cli.main:main。

from kama_claude.cli.main import main

main()
