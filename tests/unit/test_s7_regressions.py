# S7 主线回归：只使用隔离文件、fake Provider 和本机临时 TCP 服务。
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kama_claude.core.config import KamaConfig, McpServerConfig, get_config
from kama_claude.core.events.bus import EventBus
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock
from kama_claude.core.mcp.client import McpClient, McpToolError
from kama_claude.core.mcp.server import McpServerManager
from kama_claude.core.runner import AgentRunner
from kama_claude.core.session.manager import SessionManager
from kama_claude.core.session.store import SessionStore
from tests.unit.test_spawn_agent_tool import _make_tool


# 功能：Skill 参数、白名单与三层记忆确实进入模型，裸斜杠仍能续聊。
# 设计：用真实 Manager/Runner 检查 Provider 边界及磁盘原始输入，避免只测模板替换。
async def test_skill_reaches_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    skills = tmp_path / '.kama/skills'
    skills.mkdir(parents=True)
    (skills / 'probe.md').write_text(
        '---\nname: probe\nallowed_tools:\n  - list_dir\n---\n检查 $ARGUMENTS', encoding='utf-8'
    )
    (tmp_path / '.kama/context.md').write_text('项目背景', encoding='utf-8')
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(stop_reason='end_turn', text='完成'))
    store = SessionStore(tmp_path / 'sessions')
    bus = EventBus()
    manager = SessionManager(store, lambda: AgentRunner(KamaConfig(), provider=provider), bus)
    session = await manager.create('chat')
    await manager.send_message(session.id, '/probe 中文目标')
    call = provider.chat.call_args.kwargs
    assert '检查 中文目标' in call['system'] and '$ARGUMENTS' not in call['system']
    assert '项目背景' in call['system']
    assert [t['name'] for t in call['tool_schemas']] == ['list_dir']
    assert store.read_messages(session.id)[0]['content'] == '/probe 中文目标'
    await manager.send_message(session.id, '/')
    assert session.status == 'waiting_for_input' and not manager._locks[session.id].locked()
    assert store.read_messages(session.id)[-2]['content'] == '/'


@pytest.mark.parametrize('cancel_parent', [False, True])
# 功能：父 run 正常结束或取消时收拢后台子任务，下一 run 不受遗留工作影响。
# 设计：子模型停在 Event；核查注册表、子状态、事件文件、会话锁和后续历史。
async def test_runner_reaps_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_parent: bool) -> None:
    monkeypatch.chdir(tmp_path)
    entered = asyncio.Event()
    provider = MagicMock()
    calls = 0

    # 子任务等待取消；父任务按参数正常结束或等待外部取消。
    async def chat(**kw):
        nonlocal calls
        if kw['messages'][0]['content'] == 'child':
            entered.set()
            await asyncio.Event().wait()
        calls += 1
        if calls == 1:
            return LlmResponse(stop_reason='tool_use', tool_calls=[ToolCallBlock(
                'spawn', 'spawn_agent', {'description': '后台', 'prompt': 'child', 'run_in_background': True}
            )])
        if calls == 2:
            await entered.wait()
            if cancel_parent:
                await asyncio.Event().wait()
        return LlmResponse(stop_reason='end_turn', text='parent done')

    provider.chat = chat
    store = SessionStore(tmp_path / 'sessions')
    bus = EventBus()
    runners = []

    # 保存实际 Runner 供收尾后检查；生产同样每条消息创建一个实例。
    def factory():
        runner = AgentRunner(KamaConfig(), bus=bus, provider=provider)
        runners.append(runner)
        return runner

    manager = SessionManager(store, factory, bus)
    session = await manager.create('chat')
    task = asyncio.create_task(manager.send_message(session.id, 'parent'))
    await asyncio.wait_for(entered.wait(), 5)
    entries = runners[0]._task_registry.all()
    assert len(entries) == 1
    if cancel_parent:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    child_task, child_context = entries[0]
    assert child_task.cancelled() and child_context.reason == 'cancelled'
    assert not runners[0]._task_registry.all()
    assert not manager._locks[session.id].locked()
    child_file = store.runs_dir(session.id) / child_context.run_id / 'events.jsonl'
    assert child_file.exists()
    parent_file = store.runs_dir(session.id) / session.run_ids[0] / 'events.jsonl'
    events = [json.loads(line) for line in parent_file.read_text(encoding='utf-8').splitlines()]
    assert any(e['type'] == 'subagent.finished' and e['status'] == 'failed' for e in events)
    await manager.send_message(session.id, 'next')
    assert session.status == 'waiting_for_input'
    assert store.read_messages(session.id)[-1]['content'][0]['text'] == 'parent done'


# 功能：后台子 Loop 达到步数上限时查询返回错误，不能把空文本当成功。
# 设计：真实子 Loop 连续触发工具调用至上限，验证 Context 与查询结果一致。
async def test_background_failure_result(tmp_path: Path) -> None:
    from kama_claude.core.subagent.tool import AgentResultTool

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason='tool_use', tool_calls=[ToolCallBlock('x', 'missing', {})]
    ))
    tool, registry, _ = _make_tool(tmp_path, provider)
    await tool.invoke({'description': '失败', 'prompt': 'child', 'run_in_background': True})
    task, context = registry.all()[0]
    await task
    result = await AgentResultTool(registry).invoke({'run_id': context.run_id})
    assert context.status == 'failed' and result.is_error


# 功能：MCP 真实 TCP 握手、字符串 ID、通知跳过和 isError 都按协议处理。
# 设计：本地 fake server 发噪声和大响应，再断开；不依赖外部 MCP 或凭据。
async def test_mcp_tcp_roundtrip() -> None:
    closed = asyncio.Event()

    # 用 NDJSON 响应三种请求，并在客户端关闭后记录 EOF。
    async def serve(reader, writer):
        try:
            while line := await reader.readline():
                req = json.loads(line)
                if 'id' not in req:
                    continue
                method = req['method']
                if method == 'initialize':
                    result = {}
                elif method == 'tools/list':
                    result = {'tools': [{'name': 'probe', 'description': 'x' * 70000}]}
                else:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': '失败'}]}
                writer.write(b'\nnot-json\n{"method":"notice"}\n')
                writer.write((json.dumps({'id': str(req['id']), 'result': result}) + '\n').encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    client = McpClient()
    try:
        await client.connect_tcp('127.0.0.1', server.sockets[0].getsockname()[1])
        assert len((await client.list_tools())[0].description) == 70000
        with pytest.raises(McpToolError, match='失败'):
            await client.call_tool('probe', {})
    finally:
        await client.close()
        await asyncio.wait_for(closed.wait(), 5)
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize('phase', ['connect', 'list', 'cancel'])
# 功能：MCP 握手失败、发现失败或取消均关闭未登记连接。
# 设计：在不同 await 点注入错误，断言 close 和缓存，避免漏掉启动中间态。
async def test_mcp_failed_start_closes(phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.connect_tcp = AsyncMock()
    client.close = AsyncMock()
    client.list_tools = AsyncMock()
    failure = asyncio.CancelledError() if phase == 'cancel' else RuntimeError('offline')
    (client.connect_tcp if phase == 'connect' else client.list_tools).side_effect = failure
    monkeypatch.setattr('kama_claude.core.mcp.server.McpClient', lambda: client)
    manager = McpServerManager()
    if phase == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await manager.start_all([McpServerConfig(name='fake', transport='tcp')])
    else:
        await manager.start_all([McpServerConfig(name='fake', transport='tcp')])
    client.close.assert_awaited_once()
    assert manager.get_tools() == [] and manager._clients == {}


# 功能：项目 TOML 覆盖标量、追加 MCP servers；显式配置路径排除默认两层。
# 设计：在隔离 HOME/cwd 写入不同端口，直接验证生产配置入口，不读取真实 .env。
def test_project_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / 'project'
    (project / '.kama').mkdir(parents=True)
    (tmp_path / '.kama').mkdir()
    global_file = tmp_path / '.kama/config.toml'
    global_file.write_text('[core]\nport=1001\n[[mcp.servers]]\nname="global"', encoding='utf-8')
    (project / '.kama/config.toml').write_text(
        '[core]\nport=1002\n[[mcp.servers]]\nname="local"', encoding='utf-8'
    )
    monkeypatch.chdir(project)
    config = get_config()
    assert config.port == 1002 and [s.name for s in config.mcp.servers] == ['global', 'local']
    monkeypatch.setenv('KAMA_CONFIG', str(global_file))
    assert get_config().port == 1001


# 功能：thinking/signature 经 SDK 适配、Loop、磁盘和下一轮读取后保持原样。
# 设计：用 FakeStream 注入带签名块的响应，验证真实保存链而不是只测数据类。
async def test_thinking_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kama_claude.core.llm.provider import AnthropicProvider
    from tests.unit.test_llm_provider import FakeStream, _make_final

    monkeypatch.chdir(tmp_path)
    block = MagicMock(type='thinking', thinking='推理片段', signature='signed-original')
    client = MagicMock()
    client.messages.stream.side_effect = [
        FakeStream(['答案'], _make_final(content=[block])),
        FakeStream(['下一轮'], _make_final()),
    ]
    provider = AnthropicProvider('fake', client=client)
    store = SessionStore(tmp_path / 'sessions')
    manager = SessionManager(store, lambda: AgentRunner(KamaConfig(), provider=provider), EventBus())
    session = await manager.create('chat')
    await manager.send_message(session.id, '问题')
    expected = {'type': 'thinking', 'thinking': '推理片段', 'signature': 'signed-original'}
    assert store.read_messages(session.id)[1]['content'][0] == expected
    await manager.send_message(session.id, '继续')
    assert client.messages.stream.call_args.kwargs['messages'][1]['content'][0] == expected


# 功能：真实 stdio MCP 子进程握手、调用后关闭，stderr 消费任务也被回收。
# 设计：临时 Python 服务连续输出 stderr 并返回中文，核查进程 returncode 而非仅 close 返回。
async def test_mcp_stdio_cleanup(tmp_path: Path) -> None:
    import sys

    script = tmp_path / 'fake_mcp.py'
    script.write_text('''import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    print('diagnostic', file=sys.stderr, flush=True)
    result = {'content': [{'type': 'text', 'text': '中文'}]}
    print(json.dumps({'id': req['id'], 'result': result}), flush=True)
''', encoding='utf-8')
    client = McpClient()
    try:
        await client.connect_stdio(sys.executable, ['-u', str(script)])
        assert await client.call_tool('probe', {}) == '中文'
    finally:
        await client.close()
    assert client._proc is not None and client._proc.returncode is not None
    assert client._stderr_task is None


# 功能：Core 在 MCP 已启动但监听失败时仍关闭 MCP 和 trace。
# 设计：仅注入启动故障，不加载用户配置；检查外层 finally 覆盖初始化后半段。
async def test_core_start_failure_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kama_claude.core import app

    monkeypatch.chdir(tmp_path)
    config = KamaConfig()
    config.trace.file = str(tmp_path / 'trace.jsonl')
    config.mcp.servers = [McpServerConfig(name='fake')]
    manager = MagicMock()
    manager.start_all = AsyncMock()
    manager.stop_all = AsyncMock()
    server = MagicMock()
    server.start = AsyncMock(side_effect=RuntimeError('listen failure'))
    server.stop = AsyncMock()
    trace = MagicMock()
    trace.start = AsyncMock()
    trace.stop = AsyncMock()
    config.trace.enabled = True
    monkeypatch.setattr(app, 'get_config', lambda: config)
    monkeypatch.setattr(app, 'setup_logging', lambda _: None)
    monkeypatch.setattr(app, 'McpServerManager', lambda: manager)
    monkeypatch.setattr(app, 'SocketServer', lambda *a, **kw: server)
    monkeypatch.setattr(app, 'TraceWriter', lambda _: trace)
    with pytest.raises(RuntimeError, match='listen failure'):
        await app.CoreApp().run()
    manager.start_all.assert_awaited_once()
    manager.stop_all.assert_awaited_once()
    server.stop.assert_awaited_once()
    trace.stop.assert_awaited_once()
