# ═══ 文件职责：命令行多轮会话客户端 ═══
# 协作：CLI main → cmd_chat → SocketClient → CoreApp/SessionManager；不在此执行模型。
# 阅读顺序：_chat_async 看生命周期 → ChatPrinter 看事件 → _readline 看输入等待。
from __future__ import annotations

import asyncio
import sys
from typing import Any

from kama_claude.core.config import KamaConfig
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# _inline 由 token 回调更新，表示当前输出行尚未结束；不保存模型历史。
class ChatPrinter:
    # 初始化 chat 模式的流式输出状态
    def __init__(self) -> None:
        self._inline = False

    # 若当前 LLM token 尚未换行，则补一个换行
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 按事件类型打印 chat 输出和等待提示
    async def handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        elif t == "tool.call_started":
            self._ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            print("[waiting for input]")
        elif t == "session.closed":
            self._ensure_newline()
            print("session closed.")


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _readline(prompt: str) -> str:
    # input 是阻塞式内置函数；标准库 executor 用线程等键盘，主循环仍可收 socket。
    # None 选默认线程池；取消等待不保证能中断已在运行的 input。
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# 异步核心：创建 chat session，循环读取用户输入并发送到 daemon
async def _chat_async(config: KamaConfig) -> int:
    # 步骤 1：建立 TCP 连接并登记事件回调。
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = ChatPrinter()
    client.on_event(printer.handle)
    loop_task = asyncio.create_task(client.run_event_loop())
    # 步骤 2：接收循环独立推进；send_command 等待的 Future 由该循环完成。

    try:
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token"],
                "scope": "global",
            },
        )
        created = await client.send_command("session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        while True:
            # 步骤 3：一次输入触发一次 run；等待该 RPC 返回后才再次读取输入。
            try:
                line = await _readline("> ")
            except (EOFError, KeyboardInterrupt):
                break
            content = line.strip()
            if not content:
                continue
            await client.send_command(
                "session.send_message",
                {"session_id": session_id, "content": content},
            )

        await client.send_command("session.close", {"session_id": session_id})
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        # 步骤 4：取消并等待接收协程，再关闭 socket；异常同样走这里。
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0


# 执行 kama chat 命令
def cmd_chat(config: KamaConfig) -> None:
    try:
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
