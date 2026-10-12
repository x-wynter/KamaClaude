# 协议外壳测试：检查请求和响应的序列化、默认值、必要字段与错误结构。
# 这些测试直接调用模型，不会启动 TCP 服务。

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kama_claude.core.bus.envelope import (
    PARSE_ERROR,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)


# 验证请求从模型转为 JSON 再还原后，id、method、params 保持一致。
def test_request_roundtrip() -> None:
    req = JsonRpcRequest(id="1", method="core.ping", params={"client": "test"})
    # 先序列化后解析，得到新模型实例；比较的是字段值，不是对象身份。
    req2 = JsonRpcRequest.model_validate_json(req.model_dump_json())
    assert req2.id == "1"
    assert req2.method == "core.ping"
    assert req2.params == {"client": "test"}


# 验证省略 params 时由默认工厂创建空字典。
def test_request_default_params() -> None:
    req = JsonRpcRequest(id="1", method="x")
    assert req.params == {}


# 验证项目当前请求模型不接受缺少 id 的输入。
def test_request_missing_id_raises() -> None:
    # 声明预期异常；没有抛出 ValidationError 则说明输入约束没有按预期工作。
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({"jsonrpc": "2.0", "method": "x"})


# 验证 Literal["2.0"] 拒绝其他协议版本字符串。
def test_request_wrong_version_raises() -> None:
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({"jsonrpc": "1.0", "id": "1", "method": "x"})


# 验证成功响应的 id 和 result 能经 JSON 转换保留。
def test_success_roundtrip() -> None:
    resp = JsonRpcSuccess(id="1", result={"key": "value"})
    resp2 = JsonRpcSuccess.model_validate_json(resp.model_dump_json())
    assert resp2.id == "1"
    assert resp2.result == {"key": "value"}


# 验证辅助函数正确填充错误码、请求 id 和默认附加数据。
def test_make_error_sets_code() -> None:
    err = make_error("1", PARSE_ERROR, "Parse error")
    assert err.error.code == PARSE_ERROR
    assert err.id == "1"
    assert err.error.data is None


# 验证无法取得请求 id 时，可以构造 id 为 None 的错误响应。
def test_make_error_null_id() -> None:
    err = make_error(None, PARSE_ERROR, "bad json")
    assert err.id is None
