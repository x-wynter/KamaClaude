# ═══ 文件职责：命令参数与业务结果的数据模型 ═══
# 协作关系：客户端发送 params；Core 校验业务参数，返回结果后由传输层添加协议外壳。
# 阅读路线：按 ping、agent.run、event.subscribe 三组阅读；最后的 Command 是类型联合。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator


# ── 类：PingCommand ──
# 职责：连通性检查的业务参数。
# 继承 Pydantic BaseModel，获得 model_validate、model_dump 等校验/序列化方法。
class PingCommand(BaseModel):
    # 字段名 type 是协议标签；Literal 限定唯一允许值，右侧提供默认值。
    type: Literal["core.ping"] = "core.ping"
    # 没有 = 默认值，所以 client 是必填字段；缺少时校验失败。
    # 字段：调用方标识，例如 cli/0.0.1。
    client: str


# ── 类：PongResult ──
# 职责：连通性检查的业务返回值。
# 所有字段都必填；received_at 仅声明 str，并不自动校验 ISO 8601 日期格式。
class PongResult(BaseModel):
    # 字段：Core 的项目版本号。
    server_version: str
    # 字段：Core 已运行的毫秒数。
    uptime_ms: int
    # 字段：服务端处理请求时的 UTC 时间字符串。
    received_at: str  # ISO 8601


# ── 类：AgentRunCommand ──
# 职责：提交任务时的业务参数。
# goal 是任务目标；返回的 run_id 只标识已接受的任务，不是最终模型回答。
class AgentRunCommand(BaseModel):
    type: Literal["agent.run"] = "agent.run"
    # 字段：用户希望 Agent 完成的目标文本。
    goal: str


# ── 类：AgentRunResult ──
# 职责：任务受理结果，包含任务 ID，尚不是最终回答。
class AgentRunResult(BaseModel):
    # 字段：已受理任务的编号；后续事件使用同一编号。
    run_id: str


# ── 类：EventSubscribeCommand ──
# 职责：声明想接收哪些事件、哪个任务，以及是否回放历史。
# topics 决定事件种类，scope 决定任务范围，replay_from_run 决定历史来源。
# str 类型本身不会验证 scope 是否属于 global 或 run:<id>；过滤由广播器执行。
class EventSubscribeCommand(BaseModel):
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]          # fnmatch 模式，如 ["step.*", "tool.*"]
    scope: str = "global"      # "global" | "run:<run_id>"
    replay_from_run: str | None = None  # 设置则先从 events.jsonl 回放历史再接实时流


# ── 类：EventSubscribeResult ──
# 职责：返回订阅编号和历史回放条数。
class EventSubscribeResult(BaseModel):
    # 字段：本次订阅的编号，与任务 ID 不同。
    subscription_id: str
    # 字段：注册实时订阅前已发送的历史事件条数。
    replayed_count: int = 0


# 根据 type 字段决定命令类型的判别联合
# Annotated 给类型附加元数据；Discriminator 按 type 区分三个命令模型。
# 此处只声明联合类型，不执行校验；业务处理器显式调用各模型的 model_validate。
# 当前 TCP ping 处理链也没有使用这个别名来分发，它按请求 method 查字典。
Command = Annotated[
    PingCommand
    | AgentRunCommand
    | EventSubscribeCommand,
    Discriminator("type"),
]
