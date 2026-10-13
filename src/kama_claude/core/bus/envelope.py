# ═══ 文件职责：网络消息的外层结构 ═══
# 协作关系：commands/events 定义业务内容；本文件为内容补上请求 ID、结果或错误外壳。
# 阅读路线：先看请求与事件外壳，再看成功/错误响应，最后看错误码和构造函数。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 类：JsonRpcRequest ──
# 职责：客户端发往服务端的请求外壳。
# 请求模型：当前项目要求字符串 id，不支持省略 id 的通知形式。
class JsonRpcRequest(BaseModel):
    # Literal 限制值只能是 "2.0"；右侧默认值使调用方可以省略该字段。
    jsonrpc: Literal["2.0"] = "2.0"
    # 字段：请求关联编号，响应必须带回此值。
    id: str
    # 字段：服务端注册的方法名，例如 agent.run。
    method: str
    # Any 表示值类型不限。Field 是 Pydantic 的字段工具，不是 dataclasses.field。
    # default_factory=dict 为每个请求创建独立空字典，避免共享可变默认值。
    params: dict[str, Any] = Field(default_factory=dict)


# ── 类：EventPushEnvelope ──
# 职责：服务端主动推送事件时使用的外壳。
# 事件无需请求 ID，用 kind=event 区分；嵌套 event 的 type 才是 run.started 等业务名称。
class EventPushEnvelope(BaseModel):
    kind: Literal["event"] = "event"
    event: dict[str, Any]  # Event.model_dump() 的序列化结果


# ── 类：JsonRpcSuccess ──
# 职责：带原请求 ID 的成功响应。
class JsonRpcSuccess(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    # 字段：与原请求一致的编号。
    id: str
    # 字段：业务函数的返回数据，例如包含 run_id 的字典。
    result: Any


# ── 类：JsonRpcErrorObject ──
# 职责：错误码、说明和可选补充数据。
# 错误详情：code 为机器可读的错误码，message 为文字说明，data 为可选附加信息。
class JsonRpcErrorObject(BaseModel):
    code: int
    message: str
    data: Any = None


# ── 类：JsonRpcError ──
# 职责：将错误详情与原请求 ID 组合为错误响应。
# 错误响应：无法解析请求时可能拿不到 id，因此允许为 None（JSON 中的 null）。
class JsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | None = None
    # 字段：具体错误信息对象，包含 code、message 和 data。
    error: JsonRpcErrorObject


# 这些常量只是约定的整数名称；实际发送哪个错误码由调用 make_error 的位置决定。
PARSE_ERROR = -32700      # 解析错误
INVALID_REQUEST = -32600  # 请求格式错误
METHOD_NOT_FOUND = -32601 # 方法不存在
INVALID_PARAMS = -32602   # 参数错误
INTERNAL_ERROR = -32603   # 服务器内部错误


# ── 类：HandlerError ──
# 职责：携带业务错误码 code 与可选 data；传输层捕获后返回 JSON-RPC error。
# 当前 Core 的三个 handler 未主动使用它；保留为已接通的通用错误接口。
class HandlerError(Exception):
    """命令 handler 抛出此异常，SocketServer 将其转换为结构化 JSON-RPC 错误响应。"""

    # 初始化异常文本和结构化字段，super 将 message 交给 Exception 保存。
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


# ── 函数：make_error ──
# 用途：构造一个 JSON-RPC 错误响应对象
def make_error(id: str | None, code: int, message: str, data: Any = None) -> JsonRpcError:
    # 先构造内部错误详情，再放进外层响应；这里只返回对象，网络发送由传输层负责。
    return JsonRpcError(id=id, error=JsonRpcErrorObject(code=code, message=message, data=data))
