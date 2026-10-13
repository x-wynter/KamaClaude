# ═══ 文件职责：统一 trace 时间线的数据契约 ═══
# 协作关系：网络层 / EventBus 订阅者 / TracingProvider 构造记录 → TraceWriter → CLI trace。
# 阅读路线：先认 direction/layer/kind，再看可选关联字段，最后读 writer.py。
# 这是已使用的数据模型；它不会自动拦截系统内所有函数调用。

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


# ── 类：TraceRecord ──
# 第三方 Pydantic BaseModel 验证结构并提供 model_dump_json；本类不负责落盘。
class TraceRecord(BaseModel):
    # 采集点填写 UTC 时间；字段类型是 str，本身不会校验时间格式。
    ts: str
    # direction 表示数据流向；layer 表示观察层次，由不同模块的采集点填写。
    direction: Literal[
        "CLIENT→CORE",
        "CORE→CLIENT",
        "CORE",
        "CORE→LLM",
        "LLM→CORE",
    ]
    layer: Literal["ipc", "event", "llm"]
    kind: str  # command / response / error / push / event / api_call / api_response / api_error
    # 关联字段均可缺省：IPC 请求/响应通常没有顶层 run_id，不能只按 ID 看完整握手。
    run_id: str | None = None
    step: int | None = None
    client_id: str | None = None
    # data 保存该种记录的内容；Any 容许异构数据，不等于所有 kind 都有相同字段。
    data: dict[str, Any]
