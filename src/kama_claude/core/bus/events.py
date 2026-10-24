# ═══ 文件职责：运行过程中各类事件的数据模型 ═══
# 协作关系：生产者构造事件 → EventBus → 文件/广播订阅者；本文件只定义数据结构。
# 阅读路线：核心、运行、步骤、工具、模型、日志与会话事件，最后看 Event 联合。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator

# ── 共同字段约定 ──
# type：固定事件名；run_id：所属任务编号；ts：事件生成时间，通常为 UTC ISO 8601。
# BaseModel 提供字段校验与 JSON 序列化；Literal 限定 type 标签的允许值。
# 注意：CoreStartedEvent 和 LogLineEvent 目前只有定义，当前启动/日志逻辑未发布它们。

# ── 类：CoreStartedEvent ──
# 职责：描述核心服务启动时的监听地址和版本；当前只定义了这一数据结构。
class CoreStartedEvent(BaseModel):
    # Literal 限定标签只能为 core.started，供消费者区分事件类型。
    type: Literal["core.started"] = "core.started"
    # 字段：Core 的监听地址与端口。
    listen_addr: str  # e.g. "127.0.0.1:7437"
    # 字段：Core 的项目版本号。
    version: str


# ── 类：RunStartedEvent ──
# 职责：Runner 创建任务后发布，goal 是本次用户目标，run_id 用于关联后续全部事件。
class RunStartedEvent(BaseModel):
    type: Literal["run.started"] = "run.started"
    run_id: str
    # 字段：本次任务的原始目标。
    goal: str
    ts: str  # ISO 8601


# ── 类：RunFinishedEvent ──
# 职责：Runner 收尾时发布；steps 是已执行轮数，reason 允许为 None。
# status 的 str 标注不强制限定枚举值，注释列出的是业务约定。
class RunFinishedEvent(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    # 字段：任务终止状态，通常为 success 或 failed。
    status: str  # "success" | "failed"
    # 字段：失败或终止原因；没有补充原因时为 None。
    reason: str | None = None  # "exceeded_max_steps" | "cancelled" | "llm_error" | ...
    # 字段：任务累计执行的模型请求轮数。
    steps: int
    ts: str


# ── 类：StepStartedEvent ──
# 职责：Loop 每次模型请求前发布；step 是从 1 开始的当前轮数。
class StepStartedEvent(BaseModel):
    type: Literal["step.started"] = "step.started"
    run_id: str
    # 字段：当前轮次，从 1 开始。
    step: int
    ts: str


# ── 类：StepFinishedEvent ──
# 职责：正常完成本轮处理后发布；异常提前退出时不一定有这条事件。
class StepFinishedEvent(BaseModel):
    type: Literal["step.finished"] = "step.finished"
    run_id: str
    # 字段：当前轮次，从 1 开始。
    step: int
    ts: str


# ── 类：ToolCallStartedEvent ──
# 职责：工具执行入口发布；tool_use_id 关联某次调用，tool_name 用来找实现。
# params 保存调用参数；相同工具多次调用通常有不同 tool_use_id。
class ToolCallStartedEvent(BaseModel):
    type: Literal["tool.call_started"] = "tool.call_started"
    run_id: str
    # 字段：某一次工具调用的编号，用于关联开始和结果。
    tool_use_id: str
    # 字段：工具注册名称，例如 read_file。
    tool_name: str
    # 字段：本次工具调用的参数字典。
    params: dict[str, Any]
    ts: str


# ── 类：ToolCallFinishedEvent ──
# 职责：工具成功完成时发布耗时；完整工具结果仍通过 ToolResult 传给 Loop。
class ToolCallFinishedEvent(BaseModel):
    type: Literal["tool.call_finished"] = "tool.call_finished"
    run_id: str
    # 字段：某一次工具调用的编号，用于关联开始和结果。
    tool_use_id: str
    # 字段：工具注册名称，例如 read_file。
    tool_name: str
    # 字段：本次工具调用的耗时，单位毫秒。
    elapsed_ms: int
    # S3：invoke_tool 填入完整输出，TUI 将其放进可折叠工具块；默认空串兼容旧记录。
    output: str = ""  # tool result content, for TUI display
    ts: str


# ── 类：ToolCallFailedEvent ──
# 职责：工具调用失败事件；error_type 便于分类，error_message 提供具体原因。
class ToolCallFailedEvent(BaseModel):
    type: Literal["tool.call_failed"] = "tool.call_failed"
    run_id: str
    # 字段：某一次工具调用的编号，用于关联开始和结果。
    tool_use_id: str
    # 字段：工具注册名称，例如 read_file。
    tool_name: str
    # "runtime_error" | "timeout" | "schema_error" | "permission_denied" | "rate_limited"
    error_class: str
    error_message: str
    # 字段：本次工具调用的耗时，单位毫秒。
    elapsed_ms: int
    attempt: int = 1  # 1=first attempt, 2=first retry, 3=second retry
    ts: str


# ── 类：LlmTokenEvent ──
# 职责：每次收到文本片段时发布；token 字段实际保存片段字符串，而非 token 编号。
class LlmTokenEvent(BaseModel):
    type: Literal["llm.token"] = "llm.token"
    run_id: str
    # 字段：本次收到的文本片段，不一定是单个分词 token。
    token: str
    ts: str


# ── 类：LlmUsageEvent ──
# 职责：流式请求结束后发布 SDK 的用量统计，缓存计数和普通输入输出计数分字段保存。
class LlmUsageEvent(BaseModel):
    type: Literal["llm.usage"] = "llm.usage"
    run_id: str
    # 字段：SDK 返回的输入 token 数。
    input_tokens: int
    # 字段：SDK 返回的输出 token 数。
    output_tokens: int
    # 字段：本次从缓存读取的输入 token 数。
    cache_read_input_tokens: int
    # 字段：本次写入缓存的输入 token 数。
    cache_creation_input_tokens: int
    context_pct: float = 0.0
    ts: str


# ── 类：LlmModelSelectedEvent ──
# 职责：模型调用前记录本次模型与选择策略；S1 固定发布 static。
class LlmModelSelectedEvent(BaseModel):
    type: Literal["llm.model_selected"] = "llm.model_selected"
    run_id: str
    # 字段：本次使用的模型名称。
    model: str
    # 字段：模型选择策略；本阶段为 static。
    strategy: str  # "static" | "rule_based" | "cost_budget"
    ts: str


# ── 类：LogLineEvent ──
# 职责：日志事件的数据定义；S1 的 logging 并未自动连接总线来发布此模型。
class LogLineEvent(BaseModel):
    type: Literal["log.line"] = "log.line"
    run_id: str
    # 字段：日志级别。
    level: str  # "DEBUG" | "INFO" | "WARNING" | "ERROR"
    # 字段：日志来源模块或组件。
    source: str
    # 字段：日志正文。
    message: str
    ts: str


# 会话事件由 SessionManager 发布；共享 session_id/ts，不一定具有 run_id。
# created=新建，message_received=用户消息已写入，resumed=等待后的再次输入。
# waiting_for_input/closed 在 run 返回之后发布，不代表所有业务待办已完成。
class SessionCreatedEvent(BaseModel):
    type: Literal["session.created"] = "session.created"
    session_id: str
    mode: str
    ts: str


# 用户消息已追加到 thread 后发布；content 是原始输入，尚非模型结果。
class SessionMessageReceivedEvent(BaseModel):
    type: Literal["session.message_received"] = "session.message_received"
    session_id: str
    content: str
    ts: str


# 本轮 Runner 已返回，chat 可再输入；last_run_id 关联该轮，不保证该轮成功。
class SessionWaitingForInputEvent(BaseModel):
    type: Literal["session.waiting_for_input"] = "session.waiting_for_input"
    session_id: str
    last_run_id: str
    ts: str


# waiting_for_input 后收到新消息；表示续聊通知，不是从磁盘恢复会话。
class SessionResumedEvent(BaseModel):
    type: Literal["session.resumed"] = "session.resumed"
    session_id: str
    ts: str


# one_shot 自动关闭或显式 close 时发布；ts 为状态变化时间。
class SessionClosedEvent(BaseModel):
    type: Literal["session.closed"] = "session.closed"
    session_id: str
    ts: str


# 自动压缩成功事件；手动压缩使用 RPC 结果回显。
class ContextCompactedEvent(BaseModel):
    type: Literal["context.compacted"] = "context.compacted"
    session_id: str
    run_id: str
    original_tokens: int
    summary_tokens: int
    ts: str


# 调用器在等待前发布；参数供客户端展示，session/run/id 用于追踪同一调用。
class PermissionRequestedEvent(BaseModel):
    type: Literal["permission.requested"] = "permission.requested"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    param_preview: str
    session_id: str
    ts: str


# 交互允许后发布；auto_allow 的快捷路径不会额外发布此事件。
class PermissionGrantedEvent(BaseModel):
    type: Literal["permission.granted"] = "permission.granted"
    run_id: str
    tool_use_id: str
    # "allow_once" | "always_allow" | "auto_allow"
    decision: str
    ts: str


# 交互拒绝或超时后发布；静态 auto_deny 只产生工具失败事件。
class PermissionDeniedEvent(BaseModel):
    type: Literal["permission.denied"] = "permission.denied"
    run_id: str
    tool_use_id: str
    # "deny_once" | "always_deny" | "auto_deny"
    decision: str
    ts: str


# 根据 type 字段决定事件类型的判别联合
# Annotated 附加按 type 判别的元数据；S1 已有多种事件，解析时按 type 选择具体模型。
# A | B 表示可以是 A 或 B；Annotated 在类型之外附加 Pydantic 的判别规则。
# 这是类型描述，不是事件队列，也不负责发布事件或调用回调。
Event = Annotated[
    CoreStartedEvent
    | RunStartedEvent
    | RunFinishedEvent
    | StepStartedEvent
    | StepFinishedEvent
    | ToolCallStartedEvent
    | ToolCallFinishedEvent
    | ToolCallFailedEvent
    | LlmTokenEvent
    | LlmUsageEvent
    | LlmModelSelectedEvent
    | LogLineEvent
    | SessionCreatedEvent
    | SessionMessageReceivedEvent
    | SessionWaitingForInputEvent
    | SessionResumedEvent
    | SessionClosedEvent
    | ContextCompactedEvent
    | PermissionRequestedEvent
    | PermissionGrantedEvent
    | PermissionDeniedEvent,
    Discriminator("type"),
]
