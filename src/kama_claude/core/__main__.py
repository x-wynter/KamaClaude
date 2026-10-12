# python -m kama_claude.core 的入口，测试夹具也用此方式启动子进程。
# run 是同步包装函数，内部通过 asyncio.run 进入异步服务。

from kama_claude.core.app import run

run()
