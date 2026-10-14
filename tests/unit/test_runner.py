# 组装测试：注入假 provider，检查完整任务事件与落盘文件，不请求真实模型。

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from kama_claude.core.config import KamaConfig
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock
from kama_claude.core.runner import AgentRunner

# --- mock provider -----------------------------------------------------------


class _EndTurnProvider:
    """Immediately returns end_turn; no API calls made."""

    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        # 假模型立即结束，用最少步骤验证 Runner 的组装与收尾，不依赖工具执行。
        return LlmResponse(stop_reason="end_turn", text="done")


class _LoopingProvider:
    """Always returns tool_use with an unknown tool to exhaust max_steps."""

    def __init__(self) -> None:
        self._call = 0

    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        # 记录模型调用次数，便于验证配置的 max_steps 是否真正影响循环。
        self._call += 1
        tc = ToolCallBlock(id=f"t{self._call}", name="unknown_tool", input={})
        return LlmResponse(stop_reason="tool_use", tool_calls=[tc])


class _CapturingProvider:
    # 初始化捕获型 provider，保存固定响应
    def __init__(self, response: LlmResponse) -> None:
        self.response = response
        self.messages: list[dict[str, object]] = []
        self.system: str | None = None

    # 捕获本次 LLM 调用的 messages 和 system prompt
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        self.messages = [dict(m) for m in messages]
        self.system = system
        return self.response


# --- helpers -----------------------------------------------------------------


def _config(max_steps: int = 5) -> KamaConfig:
    cfg = KamaConfig()
    cfg.agent.max_steps = max_steps
    return cfg


async def _run(
    goal: str = "test goal",
    *,
    provider: object | None = None,
    config: KamaConfig | None = None,
    tmp_path: Path,
) -> list[BaseModel]:
    collected: list[BaseModel] = []

    async def _collect(e: BaseModel) -> None:
        collected.append(e)

    cfg = config or _config()
    # 注入 provider 和临时 runs_dir，真实运行 Runner 而不触碰生产任务目录。
    runner = AgentRunner(
        cfg,
        provider=provider or _EndTurnProvider(),  # type: ignore[arg-type]
        extra_handlers=[_collect],
        runs_dir=tmp_path,
    )
    await runner.run(goal)
    return collected


# --- tests -------------------------------------------------------------------


# 功能：Runner 发布目标正确的开始事件。
# 设计：注入立即结束的模型，筛选 run.started 检查 goal。
async def test_run_started_event_published(tmp_path: Path) -> None:
    events = await _run(goal="my goal", tmp_path=tmp_path)
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "run.started" in types
    started = next(e for e in events if e.type == "run.started")  # type: ignore[attr-defined]
    assert started.goal == "my goal"  # type: ignore[attr-defined]


# 功能：成功任务发布 run.finished。
# 设计：next 的默认 None 让缺失事件以明确断言失败。
async def test_run_finished_event_published_on_success(tmp_path: Path) -> None:
    events = await _run(tmp_path=tmp_path)
    # 生成器筛选匹配事件；next(..., None) 找不到时返回 None。
    finished = next(
        (e for e in events if e.type == "run.finished"), None  # type: ignore[attr-defined]
    )
    assert finished is not None
    assert finished.status == "success"  # type: ignore[attr-defined]


# 功能：达到上限后发布包含失败原因的结束事件。
# 设计：假模型持续请求工具，配置两步即可触发终止。
async def test_run_finished_event_published_on_max_steps(tmp_path: Path) -> None:
    events = await _run(
        provider=_LoopingProvider(),
        config=_config(max_steps=2),
        tmp_path=tmp_path,
    )
    # 生成器筛选匹配事件；next(..., None) 找不到时返回 None。
    finished = next(e for e in events if e.type == "run.finished")  # type: ignore[attr-defined]
    assert finished.status == "failed"  # type: ignore[attr-defined]
    assert finished.reason == "exceeded_max_steps"  # type: ignore[attr-defined]


# 功能：任务事件落盘且开始和结束记录位于首尾。
# 设计：递归找生成文件并逐行解析，验证完整组装而非单独 writer。
async def test_events_jsonl_created_with_started_and_finished(tmp_path: Path) -> None:
    await _run(tmp_path=tmp_path)
    # rglob 递归查找每个任务子目录的文件；list 把迭代结果收集起来。
    jsonl_files = list(tmp_path.rglob("events.jsonl"))
    assert len(jsonl_files) == 1
    lines = [json.loads(ln) for ln in jsonl_files[0].read_text().splitlines() if ln]
    event_types = [e["type"] for e in lines]
    assert event_types[0] == "run.started"
    assert event_types[-1] == "run.finished"


# 功能：每次任务在运行根目录下建立子目录。
# 设计：列出临时目录中的目录项，再检查事件文件位置。
async def test_run_creates_run_subdirectory(tmp_path: Path) -> None:
    await _run(tmp_path=tmp_path)
    subdirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(subdirs) == 1
    assert (subdirs[0] / "events.jsonl").exists()


# 功能：外部传入的订阅者接收到任务事件。
# 设计：用额外回调收集列表，检查 Runner 的依赖接线。
async def test_extra_handlers_receive_events(tmp_path: Path) -> None:
    secondary: list[BaseModel] = []

    async def _second(e: BaseModel) -> None:
        secondary.append(e)

    cfg = _config()
    # 注入 provider 和临时 runs_dir，真实运行 Runner 而不触碰生产任务目录。
    runner = AgentRunner(
        cfg,
        provider=_EndTurnProvider(),  # type: ignore[arg-type]
        extra_handlers=[_second],
        runs_dir=tmp_path,
    )
    await runner.run("goal")
    assert len(secondary) > 0


# 功能：配置的步数限制传到执行循环。
# 设计：检查假模型被调用次数，从实际行为验证配置传递。
async def test_config_max_steps_passed_to_loop(tmp_path: Path) -> None:
    provider = _LoopingProvider()
    await _run(provider=provider, config=_config(max_steps=3), tmp_path=tmp_path)
    assert provider._call == 3


# 功能：开始与结束事件共享非空任务 ID。
# 设计：比较两类事件的字段，确保不同环节关联同一任务。
async def test_run_id_embedded_in_started_event(tmp_path: Path) -> None:
    events = await _run(tmp_path=tmp_path)
    started = next(e for e in events if e.type == "run.started")  # type: ignore[attr-defined]
    # 生成器筛选匹配事件；next(..., None) 找不到时返回 None。
    finished = next(e for e in events if e.type == "run.finished")  # type: ignore[attr-defined]
    assert started.run_id == finished.run_id  # type: ignore[attr-defined]
    assert len(started.run_id) > 0  # type: ignore[attr-defined]


# 功能：验证注入外部 EventBus 时，runner 使用该 bus 而不自建，外部订阅者能收到所有事件
# 设计：显式传入 EventBus 实例并订阅收集器，确认 runner 不再内部新建 bus（否则外部订阅者收不到事件）；
#       这是 CoreApp 注入全局 bus 的核心行为，单元测试级别验证可避免集成测试的守护进程依赖
async def test_injected_bus_receives_events(tmp_path: Path) -> None:
    from kama_claude.core.events.bus import EventBus

    external_bus = EventBus()
    collected: list[object] = []

    async def collect(e: object) -> None:
        collected.append(e)

    external_bus.subscribe(collect)

    runner = AgentRunner(
        _config(),
        bus=external_bus,
        provider=_EndTurnProvider(),  # type: ignore[arg-type]
        runs_dir=tmp_path,
    )
    await runner.run("goal")

    types = [e.type for e in collected]  # type: ignore[attr-defined]
    assert "run.started" in types
    assert "run.finished" in types


# 功能：验证 session run 会从 thread.jsonl 预填 messages，并把 notes 注入 system prompt
# 设计：用 CapturingProvider 截获 LLM 入参，不触发真实 API；同时断言 run 目录写到 session/runs 下
async def test_session_history_and_notes_injected(tmp_path: Path) -> None:
    from kama_claude.core.session.model import Session
    from kama_claude.core.session.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    session = Session(
        id="sess-1",
        mode="chat",
        status="active",
        title="",
        created_at="t",
        updated_at="t",
    )
    store.write_meta(session)
    store.append_message("sess-1", "user", "remember python")
    store.append_note("sess-1", "Python 3.12", "run-old")

    provider = _CapturingProvider(LlmResponse(stop_reason="end_turn", text="done"))
    runner = AgentRunner(_config(), provider=provider, runs_dir=tmp_path / "runs")

    await runner.run_and_capture("remember python", run_id="run-new", session=session, store=store)

    assert provider.messages == [{"role": "user", "content": "remember python"}]
    assert provider.system is not None
    assert "Python 3.12" in provider.system
    assert (store.runs_dir("sess-1") / "run-new" / "events.jsonl").exists()
    assert not (tmp_path / "runs" / "run-new").exists()


# 功能：验证 session run 中注册了 note_save，工具调用会写入 notes.md
# 设计：mock provider 第一步请求 note_save、第二步 end_turn，覆盖 runner→registry→tool invocation 的完整路径
async def test_session_registers_note_save_tool(tmp_path: Path) -> None:
    from kama_claude.core.session.model import Session
    from kama_claude.core.session.store import SessionStore

    class _NoteProvider:
        # 初始化调用计数器，用于返回两步响应
        def __init__(self) -> None:
            self.calls = 0

        # 第一步请求 note_save，第二步返回 end_turn
        async def chat(
            self,
            messages: list[dict[str, object]],
            tool_schemas: list[dict[str, object]],
            bus: EventBus,
            run_id: str,
            *,
            step: int = 0,
            system: str | None = None,
        ) -> LlmResponse:
            self.calls += 1
            if self.calls == 1:
                return LlmResponse(
                    stop_reason="tool_use",
                    tool_calls=[
                        ToolCallBlock(
                            id="note-1",
                            name="note_save",
                            input={"content": "Use Python 3.12"},
                        )
                    ],
                )
            return LlmResponse(stop_reason="end_turn", text="noted")

    store = SessionStore(tmp_path / "sessions")
    session = Session(
        id="sess-1",
        mode="chat",
        status="active",
        title="",
        created_at="t",
        updated_at="t",
    )
    store.append_message("sess-1", "user", "remember")

    runner = AgentRunner(_config(max_steps=3), provider=_NoteProvider(), runs_dir=tmp_path)
    await runner.run_and_capture("remember", run_id="run-1", session=session, store=store)

    assert "Use Python 3.12" in store.read_notes("sess-1")
