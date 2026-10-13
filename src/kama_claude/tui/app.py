# ═══ 文件职责：终端内的事件观察界面 ═══
# 协作关系：Textual 管理界面；SocketClient 接收 Core 事件；本阶段没有提交目标的输入框。
# 阅读路线：先看界面字段与 compose，再看 on_mount/_socket_loop，最后看事件显示。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import json
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Label, RichLog

from kama_claude.core.transport.socket_client import IpcError, SocketClient


# ── 类：KamaTuiApp ──
# 职责：维护终端控件、连接状态和文本缓存，由 Textual 调度生命周期。
# 字段说明：
#   TITLE / BINDINGS / CSS：标题、快捷键和控件布局，是类级界面配置。
#   _host / _port：Core 地址；_replay_run_id：可选的历史任务 ID。
#   _token_buf：尚未写入日志控件的模型文本片段。
# App[None] 表示应用退出时不返回业务结果；类属性定义标题、快捷键和样式。
class KamaTuiApp(App[None]):
    """KamaClaude 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    TITLE = "KamaClaude TUI"
    # 库函数：Textual 的 Binding 将 q 键映射到 quit 动作；Quit 是界面中的说明。
    BINDINGS = [Binding("q", "quit", "Quit")]
    CSS = """
    Screen { layout: vertical; }
    #status {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #log { height: 1fr; }
    """

    # ── 初始化：__init__ ──
    # 用途：初始化连接参数和 token 缓冲区
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        # super 调用 Textual 父类初始化，不能只初始化自己的连接参数。
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._token_buf = ""

    # ── 函数：compose ──
    # 用途：构建 UI：顶部状态栏 + 可滚动事件日志
    def compose(self) -> ComposeResult:
        # compose 是生成器：yield 逐个把控件交给框架，而不是一次 return 列表。
        # id 用于 CSS 样式和 query_one 查询，分别对应状态栏与日志面板。
        # 库函数：Label 显示状态文字；RichLog 提供可滚动日志，markup=True 会解析 Rich 标记。
        yield Label("● connecting...", id="status")
        yield RichLog(id="log", highlight=True, markup=True)

    # ── 函数：on_mount ──
    # 用途：挂载后启动 socket 连接 worker
    def on_mount(self) -> None:
        # worker 把长期收消息的协程交给框架管理，避免 on_mount 永远不能返回。
        # exclusive 会取消同组已有 worker；退出应用时框架负责取消 worker。
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")

    # ── 函数：_socket_loop ──
    # 用途：管理 SocketClient 生命周期：连接、订阅、接收事件、断线重连
    async def _socket_loop(self) -> None:
        # 步骤 1：找到已挂载的日志与状态控件。
        # 库函数：query_one 按控件 ID 查找已挂载控件，第二个参数指定期望的控件类型。
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Label)

        # 步骤 2：每轮创建新客户端；连接失败则等待重试。
        # 外层循环管理重连；每轮新建客户端，避免复用已关闭的连接和 pending 状态。
        while True:
            client = SocketClient(self._host, self._port)
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                status.update("● not connected — retrying in 2s")
                # 异步休眠让出执行权，两秒内界面仍能处理按键和重绘。
                await asyncio.sleep(2)
                continue

            # 步骤 3：连接成功后启动接收循环。
            # 库函数：Label.update 替换状态栏文字，Textual 负责后续界面重绘。
            status.update(f"● connected  {self._host}:{self._port}")
            # 先启动接收循环，后面 send_command 才有人帮它接收订阅响应。
            loop_task = asyncio.create_task(client.run_event_loop())

            # 步骤 4：将网络事件交给界面显示方法。
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event, log)

            client.on_event(on_event)

            # 步骤 5：订阅事件，并按需附带历史回放请求。
            try:
                params: dict[str, Any] = {
                    "topics": [
                        "run.*", "step.*", "tool.*",
                        "llm.token", "llm.usage", "log.*",
                    ],
                    "scope": "global",
                }
                # 重连时仍会请求同一历史回放；S2 没有保存消费游标或去重逻辑。
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                await client.send_command("event.subscribe", params)
                # 正常断线会结束接收任务；随后 finally 关闭客户端并进入重连。
                await loop_task
            except IpcError as e:
                status.update(f"● subscribe error — {e}")

            # 步骤 6：取消接收任务，刷出剩余文本并关闭连接。
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                # 断线也刷出缓存文本，避免最后一段输出留在缓冲区。
                self._flush_tokens(log)
                await client.close()

            # 步骤 7：正常断线后等待，再开始下一次连接。
            status.update("● disconnected — retrying in 2s")
            await asyncio.sleep(2)

    # ── 函数：_flush_tokens ──
    # 用途：将 llm.token 累积缓冲区写入日志并清空
    def _flush_tokens(self, log: RichLog) -> None:
        if self._token_buf:
            # 库函数：RichLog.write 向界面追加一条日志；它不是磁盘文件写入。
            log.write(self._token_buf)
            self._token_buf = ""

    # ── 函数：_handle_event ──
    # 用途：根据事件 type 字段格式化并写入 RichLog，llm.token 累积后整体写入
    def _handle_event(self, event: dict[str, Any], log: RichLog) -> None:
        # 步骤 1：读取事件类型。
        t = event.get("type", "")

        # 步骤 2：模型文本先存入缓冲区，暂不逐片段输出。
        if t == "llm.token":
            # 逐片段追加到字符串；等非 token 事件到来再作为完整日志行写入。
            self._token_buf += event.get("token", "")
            return

        # 步骤 3：其他事件到来时，先显示之前积累的模型文本。
        self._flush_tokens(log)

        # 步骤 4：按事件类型选择显示格式并写入日志控件。
        if t == "run.started":
            log.write(
                f"[bold blue]▶ run[/bold blue]  {event.get('run_id', '')}  "
                f"{event.get('goal', '')}"
            )
        elif t == "step.started":
            log.write(f"[bold]  step {event.get('step')}[/bold]  planning...")
        elif t == "tool.call_started":
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            log.write(f"[green]  tool[/green]  {event.get('tool_name', '')}  {params_str}")
        elif t == "tool.call_finished":
            log.write(
                f"[green]  tool[/green]  {event.get('tool_name', '')} "
                f"✓  {event.get('elapsed_ms')}ms"
            )
        elif t == "tool.call_failed":
            log.write(
                f"[red]  tool[/red]  {event.get('tool_name', '')} "
                f"✗  {event.get('error_message', '')}"
            )
        elif t == "step.finished":
            log.write(f"  step {event.get('step')}  done")
        elif t == "run.finished":
            s = event.get("status", "")
            color = "green" if s == "success" else "red"
            log.write(f"[{color}]■ run[/{color}]  {s}  {event.get('steps')} steps")
        elif t == "llm.usage":
            log.write(
                f"[dim]  usage[/dim]  in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache_read={event.get('cache_read_input_tokens')}"
            )
        elif t == "log.line":
            level = event.get("level", "INFO")
            log.write(
                f"[dim]{level}[/dim]  {event.get('source', '')}  {event.get('message', '')}"
            )
