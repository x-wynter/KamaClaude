# JSON-RPC 外壳模型：请求携带 method/params，响应携带 result 或 error。
# Pydantic 是第三方库：BaseModel 子类在构建/校验时检查字段，可序列化为 JSON。

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# 请求模型：当前项目要求字符串 id，不支持省略 id 的通知形式。
class JsonRpcRequest(BaseModel):
    # Literal 限制值只能是 "2.0"；右侧默认值使调用方可以省略该字段。
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    method: str
    # Any 表示值类型不限。Field 是 Pydantic 的字段工具，不是 dataclasses.field。
    # default_factory=dict 为每个请求创建独立空字典，避免共享可变默认值。
    params: dict[str, Any] = Field(default_factory=dict)


# 事件无需请求 ID，用 kind=event 区分；嵌套 event 的 type 才是 run.started 等业务名称。
class EventPushEnvelope(BaseModel):
    kind: Literal["event"] = "event"
    event: dict[str, Any]  # Event.model_dump() 的序列化结果


class JsonRpcSuccess(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: Any


# 错误详情：code 为机器可读的错误码，message 为文字说明，data 为可选附加信息。
class JsonRpcErrorObject(BaseModel):
    code: int
    message: str
    data: Any = None


# 错误响应：无法解析请求时可能拿不到 id，因此允许为 None（JSON 中的 null）。
class JsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | None = None
    error: JsonRpcErrorObject


# 这些常量只是约定的整数名称；实际发送哪个错误码由调用 make_error 的位置决定。
PARSE_ERROR = -32700      # 解析错误
INVALID_REQUEST = -32600  # 请求格式错误
METHOD_NOT_FOUND = -32601 # 方法不存在
INVALID_PARAMS = -32602   # 参数错误
INTERNAL_ERROR = -32603   # 服务器内部错误


# 构造一个 JSON-RPC 错误响应对象
def make_error(id: str | None, code: int, message: str, data: Any = None) -> JsonRpcError:
    # 先构造内部错误详情，再放进外层响应；这里只返回对象，网络发送由传输层负责。
    return JsonRpcError(id=id, error=JsonRpcErrorObject(code=code, message=message, data=data))
