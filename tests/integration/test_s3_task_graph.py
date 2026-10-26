# S3 集成测试：任务是模型工具，不是 task.create/task.start RPC。
# 跨模块跑通 Runner → Loop → 八工具注册表 → TaskManager → 事件/trace 文件。
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kama_claude.core.config import KamaConfig
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock
from kama_claude.core.runner import AgentRunner
from kama_claude.core.trace.writer import TraceWriter
from kama_claude.core.transport.socket_client import IpcError, SocketClient


class TaskScenarioProvider:
    # 保存下一轮将要断言的任务 ID；不会创建 SDK 客户端。
    def __init__(self) -> None:
        self.parent_id = 0
        self.child_id = 0

    # 按轮次模拟模型决策，同时检查真实工具结果已回填到消息历史。
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        assert {s["name"] for s in tool_schemas} == {
            "read_file", "bash", "write_file", "list_dir",
            "task_create", "task_update", "task_list", "task_get",
            "spawn_agent", "agent_result",
        }
        params: dict[str, Any]
        if step == 1:
            name, params = "task_create", {"subject": "阅读源码🧭"}
        elif step == 2:
            parent = json.loads(messages[-1]["content"][0]["content"])
            assert parent["status"] == "pending"
            self.parent_id = parent["id"]
            name, params = "task_create", {
                "subject": "整理学习记录", "blocked_by": [self.parent_id],
            }
        elif step == 3:
            child = json.loads(messages[-1]["content"][0]["content"])
            assert child["blocked_by"] == [self.parent_id]
            self.child_id = child["id"]
            name, params = "task_update", {"task_id": self.parent_id, "status": "completed"}
        elif step == 4:
            name, params = "task_get", {"task_id": self.child_id}
        elif step == 5:
            child = json.loads(messages[-1]["content"][0]["content"])
            assert child["blocked_by"] == []
            assert child["status"] == "pending"  # 清依赖并不自动启动子任务。
            name, params = "task_list", {}
        else:
            assert "[x]" in messages[-1]["content"][0]["content"]
            assert "[ ]" in messages[-1]["content"][0]["content"]
            return LlmResponse(stop_reason="end_turn", text="学习记录完成")
        return LlmResponse(
            stop_reason="tool_use",
            tool_calls=[ToolCallBlock(id=f"call-{step}", name=name, input=params)],
        )


# 功能：验证四种任务工具在真实 Loop 中共享文件状态，完成父任务后依赖被清除。
# 设计：假模型逐轮读取上一轮工具结果，覆盖数据回填、八工具注册、UTF-8、事件与 trace。
async def test_task_tools_complete_roundtrip(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl")
    await trace.start()
    provider = TaskScenarioProvider()
    try:
        outcome = await AgentRunner(
            KamaConfig(), provider=provider, runs_dir=tmp_path, trace=trace,
        ).run_and_capture("学习 S3", run_id="s3-offline")
    finally:
        await trace.stop()
    assert outcome.status == "success"
    assert outcome.result == "学习记录完成"
    task_dir = tmp_path / "s3-offline" / ".tasks"
    parent = json.loads((task_dir / f"task_{provider.parent_id}.json").read_text(encoding="utf-8"))
    child = json.loads((task_dir / f"task_{provider.child_id}.json").read_text(encoding="utf-8"))
    assert parent["subject"] == "阅读源码🧭"
    assert parent["status"] == "completed"
    assert child["blocked_by"] == []
    events = [json.loads(s) for s in (tmp_path / "s3-offline" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [e for e in events if e["type"] == "tool.call_finished"]
    assert len(finished) == 5
    assert all(e["output"] for e in finished)
    assert events[-1]["type"] == "run.finished"
    records = [json.loads(s) for s in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["step"] for r in records if r["kind"] == "api_call"] == list(range(1, 7))
    assert sum(r["kind"] == "api_response" for r in records) == 6


# 功能：验证旧 task.* RPC 在 S3 没有注册，返回 METHOD_NOT_FOUND。
# 设计：真实本地 daemon 和 TCP 请求防止学习文档再次把内部工具写成外部命令。
@pytest.mark.parametrize("method", ["task.create", "task.list", "task.start", "task.cancel"])
async def test_task_rpc_is_not_exposed(
    running_daemon: subprocess.Popen[bytes], free_port: int, method: str,
) -> None:
    client = SocketClient("127.0.0.1", free_port)
    await client.connect()
    reader = asyncio.create_task(client.run_event_loop())
    try:
        with pytest.raises(IpcError) as caught:
            await client.send_command(method, {})
        assert caught.value.code == -32601
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await client.close()
