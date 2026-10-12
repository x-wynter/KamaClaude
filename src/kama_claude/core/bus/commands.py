# 业务模型：描述 ping、任务提交、事件订阅的参数和结果。
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


# goal 是任务目标；返回的 run_id 只标识已接受的任务，不是最终模型回答。
class AgentRunCommand(BaseModel):
    type: Literal["agent.run"] = "agent.run"
    goal: str


class AgentRunResult(BaseModel):
    run_id: str


# topics 决定事件种类，scope 决定任务范围，replay_from_run 决定历史来源。
# str 类型本身不会验证 scope 是否属于 global 或 run:<id>；过滤由广播器执行。
class EventSubscribeCommand(BaseModel):
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]          # fnmatch 模式，如 ["step.*", "tool.*"]
    scope: str = "global"      # "global" | "run:<run_id>"
    replay_from_run: str | None = None  # 设置则先从 events.jsonl 回放历史再接实时流


class EventSubscribeResult(BaseModel):
    subscription_id: str
    replayed_count: int = 0


# 根据 type 字段决定命令类型的判别联合
# Annotated 给类型附加元数据；Discriminator 按 type 区分三个命令模型。
# 此处只声明联合类型，不执行校验；业务处理器显式调用各模型的 model_validate。
# 当前 TCP ping 处理链也没有使用这个别名来分发，它按请求 method 查字典。
Command = Annotated[
    PingCommand | AgentRunCommand | EventSubscribeCommand,
    Discriminator("type"),
]
