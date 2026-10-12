# 业务模型测试：验证必填字段、默认标签，以及序列化后能否还原相同数据。
# roundtrip 表示往返转换；assert 为 False 时 pytest 将该用例判为失败。

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kama_claude.core.bus.commands import PingCommand, PongResult
from kama_claude.core.bus.events import CoreStartedEvent


# 验证 PingCommand 转成 JSON 再读回后，client 和 type 得到保留。
def test_ping_command_roundtrip() -> None:
    cmd = PingCommand(client="cli/0.0.1")
    # 内层 model_dump_json 返回字符串；外层 model_validate_json 解析并校验为新对象。
    cmd2 = PingCommand.model_validate_json(cmd.model_dump_json())
    assert cmd2.client == "cli/0.0.1"
    assert cmd2.type == "core.ping"


# 验证省略 type 参数时自动使用 core.ping。
def test_ping_command_default_type() -> None:
    cmd = PingCommand(client="x")
    assert cmd.type == "core.ping"


# 验证必填 client 缺失时触发 Pydantic ValidationError。
def test_ping_command_missing_client_raises() -> None:
    # 只有代码块抛出预期异常才算通过；这是测试错误分支的常用写法。
    with pytest.raises(ValidationError):
        PingCommand.model_validate({})


# 验证 PongResult 经 Python 字典往返转换后保留服务版本和运行时长。
def test_pong_result_roundtrip() -> None:
    pong = PongResult(server_version="0.0.1", uptime_ms=42, received_at="2026-05-11T00:00:00Z")
    # model_dump 返回字典；model_validate 接收 Python 数据，与 JSON 字符串接口不同。
    pong2 = PongResult.model_validate(pong.model_dump())
    assert pong2.server_version == "0.0.1"
    assert pong2.uptime_ms == 42


# 验证启动事件模型的 JSON 往返；不表示运行时已经发布此事件。
def test_core_started_event_roundtrip() -> None:
    evt = CoreStartedEvent(listen_addr="127.0.0.1:7437", version="0.0.1")
    evt2 = CoreStartedEvent.model_validate_json(evt.model_dump_json())
    assert evt2.listen_addr == "127.0.0.1:7437"
    assert evt2.type == "core.started"
