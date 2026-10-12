# CLI 客户端：连接 core，发送一行 JSON 请求，读取一行响应并打印结果。
# asyncio/json/sys/time 属于标准库；响应模型来自项目，校验能力来自 Pydantic。

from __future__ import annotations

import asyncio
import json
import sys
import time

import kama_claude
from kama_claude.core.bus.commands import PongResult
from kama_claude.core.bus.envelope import JsonRpcError, JsonRpcSuccess
from kama_claude.core.config import KamaConfig


# 同步入口：运行 ping 协程，连接失败时打印错误并退出
def cmd_ping(config: KamaConfig) -> None:
    try:
        # _ping 是 async def 函数：调用先得到协程对象；run 创建事件循环并驱动它完成。
        # 当前同步函数会等待 run 返回，不是发起任务后立即继续。
        asyncio.run(_ping(config))
    # 捕获元组中任意一种异常；ConnectionRefusedError 已经是 OSError 的子类。
    # OSError 不只表示服务未启动；超时等其他系统错误也可能进入此分支。
    except (ConnectionRefusedError, OSError):
        # f-string 将花括号中的属性值插入文字；stderr 是错误输出流。
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        # 以失败状态结束命令；不同于 return，它可以使整个进程退出。
        sys.exit(1)


# 向 core 守护进程发送 ping 请求，打印 pong 响应及延迟
# async def 定义协程函数；await 等待异步操作，并允许事件循环调度其他任务。
async def _ping(config: KamaConfig) -> None:
    # monotonic 是单调时钟，适合计算时间差，不受系统时钟校准影响。
    t0 = time.monotonic()
    # await 等待 TCP 连接建立；元组解包分别取得读流和写流。
    reader, writer = await asyncio.open_connection(config.host, config.port)

    # 这是普通字典。id 用于匹配响应，method 是服务端注册的方法名。
    # params 保存业务参数；这里并未先创建 PingCommand 模型。
    req = {
        "jsonrpc": "2.0",
        "id": "cli-1",
        "method": "core.ping",
        "params": {"client": f"cli/{kama_claude.__version__}"},
    }
    # dumps 把字典变成 JSON 字符串，encode 默认编码为 UTF-8 字节。
    # TCP 没有消息边界，所以追加换行符，让服务端按行分割消息。
    writer.write((json.dumps(req) + "\n").encode())
    # write 先提交数据到传输缓冲区；drain 在需要时等待缓冲区缓解压力。
    # 它不保证对端已收到或处理消息。
    await writer.drain()

    # readline 等到换行或连接结束；wait_for 将这一次读取的等待限制为 10 秒。
    # 这不是对整个 ping 流程设置 10 秒上限。
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    # 秒差乘 1000 转为毫秒；int 去掉小数部分。此时间包含连接和请求响应。
    latency_ms = int((time.monotonic() - t0) * 1000)

    # close 发起关闭；wait_closed 等待关闭完成。这里位于正常读取路径中。
    writer.close()
    await writer.wait_closed()

    # loads 将 JSON 字节解析成 Python 数据；与发送时的 dumps 方向相反。
    raw = json.loads(line)
    # 字典的 in 检查键是否存在；服务端错误响应带 error，成功响应带 result。
    if "error" in raw:
        # model_validate 是 Pydantic 模型的方法：校验输入并创建可按属性访问的对象。
        err = JsonRpcError.model_validate(raw)
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        sys.exit(1)

    # 分两层校验：先校验 JSON-RPC 外壳，再将 result 校验成业务响应 PongResult。
    resp = JsonRpcSuccess.model_validate(raw)
    result = PongResult.model_validate(resp.result)
    print(f"pong server={result.server_version} uptime={result.uptime_ms}ms latency={latency_ms}ms")
