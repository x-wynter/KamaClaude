# 业务模型：PingCommand 描述 ping 参数，PongResult 描述返回结果。
# 它们不是完整 JSON-RPC 外壳；外壳定义在 envelope.py。

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator


# 继承 Pydantic BaseModel，获得 model_validate、model_dump 等校验/序列化方法。
class PingCommand(BaseModel):
    # 字段名 type 是协议标签；Literal 限定唯一允许值，右侧提供默认值。
    type: Literal["core.ping"] = "core.ping"
    # 没有 = 默认值，所以 client 是必填字段；缺少时校验失败。
    client: str


# 所有字段都必填；received_at 仅声明 str，并不自动校验 ISO 8601 日期格式。
class PongResult(BaseModel):
    server_version: str
    uptime_ms: int
    received_at: str  # ISO 8601


# 根据 type 字段决定命令类型的判别联合
# Annotated 给类型附加元数据；Discriminator 指定未来按 type 区分不同命令。
# 当前只有一个模型，并不是实际的多成员联合；这里只赋值别名，不执行校验。
# 当前 TCP ping 处理链也没有使用这个别名来分发，它按请求 method 查字典。
Command = Annotated[
    PingCommand,
    Discriminator("type"),
]
