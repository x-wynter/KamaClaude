# ═══ 文件职责：命令参数与业务结果的数据模型 ═══
# 协作关系：客户端发送 params；Core 校验业务参数，返回结果后由传输层添加协议外壳。
# 阅读路线：ping → agent.run → event.subscribe → 五组 session 命令与权限回应；最后看类型联合。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator

from kama_claude.core.session.model import SessionMode, SessionStatus


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


# 以下四对模型由 CoreApp handler 校验/返回；声明模型本身不会注册 RPC。
# session_id 定位会话，run_id 定位其中一轮；二者不可互换。
class SessionCreateCommand(BaseModel):
    type: Literal["session.create"] = "session.create"
    mode: SessionMode = "chat"  # 客户端选择；Manager 据此决定一轮后关闭还是等待。
    title: str = ""  # 留空则 Manager 用首条消息补标题。


# Manager 创建后的标识与状态；客户端保存 session_id 用于后续操作。
class SessionCreateResult(BaseModel):
    session_id: str
    status: SessionStatus


# 一条新用户输入；历史由服务端 Store 补齐，客户端无需再次发送完整对话。
class SessionSendMessageCommand(BaseModel):
    type: Literal["session.send_message"] = "session.send_message"
    session_id: str
    content: str


# 此响应在本轮 run 完成后返回 ID；最终文本从事件流或 get_history 读取。
class SessionSendMessageResult(BaseModel):
    run_id: str


# 指定要读取的会话；Manager 先验证它在本进程索引中存在。
class SessionGetHistoryCommand(BaseModel):
    type: Literal["session.get_history"] = "session.get_history"
    session_id: str


# messages 是 role/content 列表，不是 events.jsonl 或 notes.md。
class SessionGetHistoryResult(BaseModel):
    messages: list[dict[str, Any]]


# 请求关闭会话；忙时拒绝，不代表取消当前 run。
class SessionCloseCommand(BaseModel):
    type: Literal["session.close"] = "session.close"
    session_id: str


# 关闭成功后的状态，当前 handler 固定返回 closed。
class SessionCloseResult(BaseModel):
    status: SessionStatus


# 审批回应只关联 tool_use_id；Core 校验字段类型后交 Manager；decision 未限制枚举值。
class PermissionRespondCommand(BaseModel):
    type: Literal["permission.respond"] = "permission.respond"
    tool_use_id: str
    # "allow_once" | "always_allow" | "deny_once" | "always_deny"
    decision: str


# RPC 回应仅确认 handler 返回；不包含原工具的执行结果。
class PermissionRespondResult(BaseModel):
    ok: bool = True


# focus 可指定摘要重点；TUI 当前固定传空字符串。
class SessionCompactCommand(BaseModel):
    type: Literal["session.compact"] = "session.compact"
    session_id: str
    focus: str = ""


class SessionCompactResult(BaseModel):
    summary_tokens: int
    saved_tokens: int


# 根据 type 字段决定命令类型的判别联合
# Annotated 给类型附加元数据；Discriminator 按 type 区分九个命令模型。
# 此处只声明联合类型，不执行校验；业务处理器显式调用各模型的 model_validate。
# 当前 TCP ping 处理链也没有使用这个别名来分发，它按请求 method 查字典。
Command = Annotated[
    PingCommand
    | AgentRunCommand
    | EventSubscribeCommand
    | SessionCreateCommand
    | SessionSendMessageCommand
    | SessionGetHistoryCommand
    | SessionCloseCommand
    | PermissionRespondCommand
    | SessionCompactCommand,
    Discriminator("type"),
]
