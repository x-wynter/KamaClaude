# 集成测试：依赖 running_daemon 启动真实 core，通过 TCP 检查完整请求/响应。
# pytest-asyncio 按项目 auto 配置运行 async def 测试；assert 不满足时测试失败。

from __future__ import annotations

import asyncio
import json
import subprocess


# 验证正常 ping 返回版本、运行时长、时间，以及与请求一致的 id。
async def test_ping_returns_pong(
    # 即使函数体不引用此参数，声明它也会触发夹具启动服务，并在测试后清理。
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    req = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "core.ping",
        "params": {"client": "test/0.0.1"},
    }
    # 与 CLI 一样：字典 → JSON 字符串 → UTF-8 字节；换行符标记消息结束。
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()

    # 为读取响应设置超时，避免无响应时无限等待；随后关闭本次连接。
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    writer.close()
    await writer.wait_closed()

    # 直接解析响应字典并断言协议字段，用实际收发结果验证整条通信链。
    resp = json.loads(line)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == "test-1"
    assert "result" in resp
    assert resp["result"]["server_version"] == "0.0.1"
    assert resp["result"]["uptime_ms"] >= 0
    assert "received_at" in resp["result"]


# 发送未注册的方法名，验证服务端返回 -32601，而不是成功结果。
async def test_unknown_method_returns_error(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    req = {
        "jsonrpc": "2.0",
        "id": "test-2",
        "method": "core.nonexistent",
        "params": {},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()

    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    writer.close()
    await writer.wait_closed()

    resp = json.loads(line)
    assert "error" in resp
    assert resp["error"]["code"] == -32601  # METHOD_NOT_FOUND


# 发送不合法的 JSON 字节，验证解析错误被转换成 -32700 协议响应。
async def test_invalid_json_returns_error(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    writer.write(b"not valid json\n")
    await writer.drain()

    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    writer.close()
    await writer.wait_closed()

    resp = json.loads(line)
    assert "error" in resp
    assert resp["error"]["code"] == -32700  # PARSE_ERROR
