# 事件数据模型：描述 core 启动事件的数据形状，供模型测试和协议文档使用。
# 当前 CoreApp.run 没有发布此事件；定义模型不等于已经实现发送逻辑。

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator


# BaseModel 提供校验和序列化；listen_addr 和 version 没有默认值，必须提供。
class CoreStartedEvent(BaseModel):
    # Literal 限定标签只能为 core.started，供消费者区分事件类型。
    type: Literal["core.started"] = "core.started"
    listen_addr: str  # e.g. "127.0.0.1:7437"
    version: str


# 根据 type 字段决定事件类型的判别联合
# Annotated 附加按 type 判别的元数据；当前只有一个事件模型，并非多成员联合。
Event = Annotated[
    CoreStartedEvent,
    Discriminator("type"),
]
