# 事件数据模型：描述任务、步骤、工具和模型调用的可观察记录。
# 当前 CoreApp.run 没有发布此事件；定义模型不等于已经实现发送逻辑。

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator


# BaseModel 提供校验和序列化；listen_addr 和 version 没有默认值，必须提供。
class CoreStartedEvent(BaseModel):
    # Literal 限定标签只能为 core.started，供消费者区分事件类型。
    type: Literal["core.started"] = "core.started"
    listen_addr: str  # e.g. "127.0.0.1:7437"
    version: str


# Runner 创建任务后发布，goal 是本次用户目标，run_id 用于关联后续全部事件。
class RunStartedEvent(BaseModel):
    type: Literal["run.started"] = "run.started"
    run_id: str
    goal: str
    ts: str  # ISO 8601


# Runner 收尾时发布；steps 是已执行轮数，reason 允许为 None。
# status 的 str 标注不强制限定枚举值，注释列出的是业务约定。
class RunFinishedEvent(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    status: str  # "success" | "failed"
    reason: str | None = None  # "exceeded_max_steps" | "cancelled" | "llm_error" | ...
    steps: int
    ts: str


# Loop 每次模型请求前发布；step 是从 1 开始的当前轮数。
class StepStartedEvent(BaseModel):
    type: Literal["step.started"] = "step.started"
    run_id: str
    step: int
    ts: str


# 正常完成本轮处理后发布；异常提前退出时不一定有这条事件。
class StepFinishedEvent(BaseModel):
    type: Literal["step.finished"] = "step.finished"
    run_id: str
    step: int
    ts: str


# 工具执行入口发布；tool_use_id 关联某次调用，tool_name 用来找实现。
# params 保存调用参数；相同工具多次调用通常有不同 tool_use_id。
class ToolCallStartedEvent(BaseModel):
    type: Literal["tool.call_started"] = "tool.call_started"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    ts: str


# 工具成功完成时发布耗时；完整工具结果仍通过 ToolResult 传给 Loop。
class ToolCallFinishedEvent(BaseModel):
    type: Literal["tool.call_finished"] = "tool.call_finished"
    run_id: str
    tool_use_id: str
    tool_name: str
    elapsed_ms: int
    ts: str


# 工具调用失败事件；error_type 便于分类，error_message 提供具体原因。
class ToolCallFailedEvent(BaseModel):
    type: Literal["tool.call_failed"] = "tool.call_failed"
    run_id: str
    tool_use_id: str
    tool_name: str
    error_type: str  # "runtime_error" | "timeout" | "schema_error"
    error_message: str
    elapsed_ms: int
    ts: str


# 每次收到文本片段时发布；token 字段实际保存片段字符串，而非 token 编号。
class LlmTokenEvent(BaseModel):
    type: Literal["llm.token"] = "llm.token"
    run_id: str
    token: str
    ts: str


# 流式请求结束后发布 SDK 的用量统计，缓存计数和普通输入输出计数分字段保存。
class LlmUsageEvent(BaseModel):
    type: Literal["llm.usage"] = "llm.usage"
    run_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    ts: str


# 模型调用前记录本次模型与选择策略；S1 固定发布 static。
class LlmModelSelectedEvent(BaseModel):
    type: Literal["llm.model_selected"] = "llm.model_selected"
    run_id: str
    model: str
    strategy: str  # "static" | "rule_based" | "cost_budget"
    ts: str


# 日志事件的数据定义；S1 的 logging 并未自动连接总线来发布此模型。
class LogLineEvent(BaseModel):
    type: Literal["log.line"] = "log.line"
    run_id: str
    level: str  # "DEBUG" | "INFO" | "WARNING" | "ERROR"
    source: str
    message: str
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
    | LogLineEvent,
    Discriminator("type"),
]
