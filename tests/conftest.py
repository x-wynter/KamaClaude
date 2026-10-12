# pytest 共享设置与夹具：pytest 会发现 conftest.py，不需测试文件手动导入。
# 测试参数写出夹具名称，pytest 就创建并注入相应资源。

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator

import pytest


# 将默认测试临时目录放入项目缓存，保留用户显式指定的临时目录设置
def pytest_configure(config: pytest.Config) -> None:
    # pytest_configure 是 pytest 启动钩子；只有用户未指定临时路径时设置默认值。
    if config.option.basetemp is None and "PYTEST_DEBUG_TEMPROOT" not in os.environ:
        temp_root = config.rootpath / ".pytest_cache" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(temp_root)
        # lambda 定义匿名函数，留到清理时执行；pop 的默认值 None 避免键不存在时报错。
        config.add_cleanup(lambda: os.environ.pop("PYTEST_DEBUG_TEMPROOT", None))


@pytest.fixture
# 申请操作系统分配的空闲 TCP 端口，关闭探测 socket 后返回端口号。
def free_port() -> int:
    # AF_INET 表示 IPv4，SOCK_STREAM 表示 TCP；with 结束时自动关闭 socket。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # 端口 0 让操作系统选择端口；getsockname 返回实际绑定的地址和端口元组。
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # 返回前 socket 已关闭；端口并未被持续保留，随后由 daemon 重新绑定。
    return port  # socket released; daemon can bind to this port


@pytest.fixture
# 异步生成器夹具：yield 前准备服务，yield 将进程交给测试，之后做清理。
# AsyncGenerator 的第一个类型参数是产出类型，第二个是接收值类型。
async def running_daemon(free_port: int) -> AsyncGenerator[subprocess.Popen[bytes], None]:
    # 复制环境再修改，作为子进程环境，不直接更改父进程的 os.environ。
    env = os.environ.copy()
    env["KAMA_PORT"] = str(free_port)
    env["KAMA_LOG_FILE"] = ""
    env["KAMA_LOG_LEVEL"] = "WARNING"

    # Popen 启动子进程后立即返回；sys.executable 保证使用当前测试解释器。
    # 参数用列表传入，无需拼接 shell 命令；-m 表示按模块名运行。
    proc = subprocess.Popen([sys.executable, "-m", "kama_claude.core"], env=env)

    # 计算启动截止时刻；轮询真实连接成功与否，避免仅凭固定延时判断就绪。
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        # 异步等待 0.05 秒，期间让事件循环处理其他任务。
        await asyncio.sleep(0.05)
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
            writer.close()
            await writer.wait_closed()
            break
        except (ConnectionRefusedError, OSError):
            pass
    # while 的 else 只在循环未通过 break 退出时执行，这里代表超时仍未连接成功。
    else:
        proc.terminate()
        proc.wait()
        pytest.fail("Daemon did not start within 3 seconds")

    # 暂停夹具并运行使用它的测试；测试结束后恢复执行下面的进程清理。
    yield proc

    proc.terminate()
    try:
        # wait 等待子进程结束；超时则改用 kill，并再次 wait 回收进程。
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
