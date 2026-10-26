# ═══ 文件职责：把多次 run 串成一个会话 ═══
# 位置：CoreApp 的 session.* handler 与 AgentRunner 之间；不替代 Loop 的模型决策。
# 协作：Session 保存元数据，SessionStore 保存文件，runner_factory 按需创建 Runner。
# 阅读顺序：model → store → create → send_message → close → runner。
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kama_claude.core.bus.envelope import HandlerError
from kama_claude.core.bus.events import (
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionMessageReceivedEvent,
    SessionResumedEvent,
    SessionWaitingForInputEvent,
    SkillInvokedEvent,
)
from kama_claude.core.events.bus import EventBus
from kama_claude.core.runs import new_run_id
from kama_claude.core.session.model import Session, SessionMode
from kama_claude.core.session.store import SessionStore
from kama_claude.core.skills.loader import SkillLoader

if TYPE_CHECKING:
    # typing 的常量只在类型检查时为真，避免运行时循环导入 Runner 与 session 包。
    from kama_claude.core.llm.base import LLMProvider
    from kama_claude.core.runner import AgentRunner

SESSION_NOT_FOUND = -32010
SESSION_CLOSED = -32011
SESSION_BUSY = -32012


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# _sessions 是本进程创建的会话索引；启动时不会扫描磁盘恢复旧会话。
# _locks 每个 session 一把 asyncio.Lock；同会话串行，不同会话仍可交替执行。
# Callable[[], AgentRunner] 表示无参数工厂；保存函数，不是在初始化时创建 Runner。
class SessionManager:
    # 初始化会话管理器，接入文件存储、runner 工厂、事件总线和可选的 LLM provider（用于手动压缩）
    def __init__(
        self,
        store: SessionStore,
        runner_factory: Callable[[], AgentRunner],
        bus: EventBus,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], LLMProvider] | None = None,
    ) -> None:
        self._store = store
        self._runner_factory = runner_factory
        self._bus = bus
        self._provider = provider
        self._provider_factory = provider_factory
        self._sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._skill_loader = SkillLoader()

    # 创建新 session 并写入 meta.json
    async def create(self, mode: SessionMode, title: str = "") -> Session:
        # 步骤 1：标准库 uuid 生成随机标识；时间字段供元数据和事件使用。
        sid = f"sess-{uuid.uuid4().hex[:12]}"
        ts = _now()
        session = Session(
            id=sid,
            mode=mode,
            status="active",
            title=title,
            created_at=ts,
            updated_at=ts,
            run_ids=[],
        )
        self._sessions[sid] = session
        # 步骤 2：登记对象与锁、写元数据，然后通知订阅者；尚未请求模型。
        self._locks[sid] = asyncio.Lock()
        self._store.write_meta(session)
        await self._bus.publish(SessionCreatedEvent(session_id=sid, mode=mode, ts=ts))
        return session

    # 处理用户消息，追加 thread 并启动一次 agent run
    async def send_message(self, sid: str, content: str, *, run_id: str | None = None) -> str:
        # 步骤 1：查找内存对象；忙时直接报错而不是排队等待上一轮。
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")

        async with lock:
            # async with 在正常、异常、取消路径都释放锁；它不会回滚已写文件。
            if session.status == "closed":
                raise HandlerError(SESSION_CLOSED, "session already closed")

            if session.status == "waiting_for_input":
                await self._bus.publish(SessionResumedEvent(session_id=sid, ts=_now()))

            self._store.append_message(sid, "user", content)
            # 步骤 2：先持久化用户消息，Runner 随后读完整历史，避免重复追加 goal。
            await self._bus.publish(
                SessionMessageReceivedEvent(session_id=sid, content=content, ts=_now())
            )

            if not session.title:
                session.title = content[:40]

            run_id = run_id or new_run_id()
            session.run_ids.append(run_id)
            session.updated_at = _now()
            self._store.write_meta(session)

            # Skill 解析：检测 "/" 前缀，展开为系统提示覆盖和工具白名单
            goal = content
            system_prompt_override: str | None = None
            tool_whitelist: list[str] | None = None
            # 原输入已落盘；展开文本作为本轮 system，历史仍保留用户的斜杠命令。
            # Skill 不是新 RPC，未命中名称会走普通会话；裸 / 也按普通消息处理。
            if content.startswith("/"):
                parts = content[1:].split(None, 1)
                skill_name = parts[0] if parts else ""
                arguments = parts[1] if len(parts) > 1 else ""
                skill = self._skill_loader.resolve(skill_name)
                if skill is not None:
                    goal = self._skill_loader.render_prompt(skill, arguments)
                    system_prompt_override = goal
                    tool_whitelist = skill.allowed_tools or None
                    await self._bus.publish(
                        SkillInvokedEvent(
                            skill_name=skill_name,
                            arguments=arguments,
                            run_id=run_id,
                            ts=_now(),
                        )
                    )

            runner = self._runner_factory()
            # 步骤 3：创建本轮 Runner 并等待其结束；普通 await 不产生新线程。
            # RunOutcome 在这里未检查，所以失败的 run 也可进入 waiting_for_input。
            await runner.run_and_capture(
                goal,
                run_id=run_id,
                session=session,
                store=self._store,
                system_prompt_override=system_prompt_override,
                tool_whitelist=tool_whitelist,
            )

            session.updated_at = _now()
            # 步骤 4：one_shot 自动关闭；chat 等下一条输入，没有自动下一轮。
            # CancelledError 会跳过这里：锁仍释放，但不保证产生等待/关闭事件。
            if session.mode == "one_shot":
                session.status = "closed"
                await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))
            else:
                session.status = "waiting_for_input"
                await self._bus.publish(
                    SessionWaitingForInputEvent(
                        session_id=sid,
                        last_run_id=run_id,
                        ts=session.updated_at,
                    )
                )
            self._store.write_meta(session)
            return run_id

    # 关闭指定 session 并更新 meta.json
    async def close(self, sid: str) -> None:
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        async with lock:
            session.status = "closed"
            session.updated_at = _now()
            self._store.write_meta(session)
            await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))

    # 输入 sid/focus，输出 token 统计；与 send_message 共用锁，忙时拒绝。
    # await 期间持锁，取消自动释放且不写 thread；手动路径不发布自动压缩事件。
    # 手动压缩指定 session 的 thread，将摘要持久化写入 thread.jsonl
    async def compact(self, sid: str, focus: str = "") -> Any:
        self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        # 学习修复：首次使用才创建模型适配器，保持无凭据 daemon 可启动。
        if self._provider is None and self._provider_factory is not None:
            self._provider = self._provider_factory()
        if self._provider is None:
            raise HandlerError(-32020, "provider not available for compaction")
        async with lock:
            from kama_claude.core.bus.commands import SessionCompactResult
            from kama_claude.core.compact.compactor import Compactor

            messages = self._store.read_messages(sid)
            session_dir = self._store.session_dir(sid)
            compactor = Compactor(self._bus, session_dir, sid)
            result = await compactor.compact_messages(messages, self._provider, focus=focus)
            if result is None:
                raise HandlerError(-32021, "compaction failed or not beneficial")
            self._store.write_compacted(
                sid,
                [
                    {"role": "user", "content": result.summary_text},
                    {
                        "role": "assistant",
                        "content": "Understood, I'll continue from this summary.",
                    },
                ],
            )
            return SessionCompactResult(
                summary_tokens=result.summary_tokens,
                saved_tokens=max(0, result.original_token_estimate - result.summary_tokens),
            )

    # 读取指定 session 的完整 thread 历史
    async def get_history(self, sid: str) -> list[dict[str, Any]]:
        self._get_session(sid)
        return self._store.read_messages(sid)

    # 从内存索引取 session，不存在时抛 JSON-RPC 结构化错误
    def _get_session(self, sid: str) -> Session:
        session = self._sessions.get(sid)
        if session is None:
            raise HandlerError(SESSION_NOT_FOUND, "session not found")
        return session
