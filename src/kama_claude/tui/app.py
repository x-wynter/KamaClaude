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
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

from kama_claude.core.config import KamaConfig
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# 返回最多 n 个字符的预览，超出时加省略号；完整内容仍保存在控件字段中。
def _preview(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# 标准库 json.dumps 将参数字典转为可显示文本；ensure_ascii=False 保留中文。
def _params_str(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False)


# ── 类：LLMStreamBlock ──
# 职责：一个文本控件对应一段连续 token 流；_text 由 append_token 追加。
# Static 是第三方 Textual 控件；update 修改显示内容而不是磁盘文件。
class LLMStreamBlock(Static):
    """在同一个 Static widget 中累积 LLM 流式 token。"""

    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    # 初始化为空文本块
    def __init__(self) -> None:
        super().__init__("")
        self._text = ""

    # 追加一个 token 并刷新显示
    def append_token(self, token: str) -> None:
        self._text += token
        self.update(self._text)


# ── 类：ToolCallBlock ──
# 职责：显示一次工具调用的参数、结果和耗时，完成后可点击展开。
# _tool_name/_params/_params_full 在开始时保存；_output/_elapsed_ms/_is_error 在结束时更新。
# _finished 初始 False；set_result 设为 True 后点击才有效；expanded CSS 类控制详情可见性。
# 此类是界面控件，与 core/llm/types.py 的同名数据类不是同一对象。
class ToolCallBlock(Widget):
    """可折叠的工具调用块：折叠时显示摘要，点击后展开完整 params 和 output。"""

    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 0; }
    ToolCallBlock > .detail { display: none; padding: 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; }
    """

    # 初始化工具调用信息
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._params_full = _params_str(params)
        self._output = ""
        self._elapsed_ms = 0
        self._is_error = False
        self._finished = False

    # Textual 调用的生成器：yield 逐个交出子控件；不是一次性返回一个列表。
    def compose(self) -> ComposeResult:
        yield Static(self._summary(), classes="summary")
        yield Static("", classes="detail")

    # 生成摘要行文本
    def _summary(self) -> str:
        params_pre = _preview(self._params_full, 60)
        icon = "[bold yellow]✎[/bold yellow]"
        line = f"  {icon} [bold]{self._tool_name}[/bold]  [dim]{params_pre}[/dim]"
        if self._finished:
            out_pre = _preview(self._output, 50)
            color = "red" if self._is_error else "dim"
            hint = "  [dim]▸ click to expand[/dim]" if len(self._output) > 50 else ""
            line += (
                f"\n  [dim]↳[/dim] [{color}]{out_pre}[/{color}]"
                f"  [dim]{self._elapsed_ms}ms[/dim]{hint}"
            )
        return line

    # 工具调用完成时更新结果并刷新摘要（widget 未挂载时跳过 DOM 更新）
    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        self._output = output
        self._elapsed_ms = elapsed_ms
        self._is_error = is_error
        self._finished = True
        # Textual children 表示已挂载子节点；单元测试可先创建未挂载的块并检查数据。
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    # 点击时切换展开/折叠状态
    def on_click(self) -> None:
        if not self._finished:
            return
        # query_one 按 CSS 类查子控件；add_class/remove_class 触发相应 CSS 显示规则。
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params:[/dim]\n    {self._params_full}\n"
                f"[dim]output:[/dim]\n    {self._output}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


# ── 类：KamaTuiApp ──
# 职责：管理连接与事件到控件的映射；App[None] 表示退出时不返回业务值。
# _host/_port 是地址，_replay_run_id 是可选历史，_client 是当前连接或 None。
# _current_llm 指向连续文本块；_pending_tool_blocks 按 tool_use_id 保存待完成控件。
# TITLE/BINDINGS/CSS 是类级配置：q 退出，log-view 滚屏；S3 没有输入框。
class KamaTuiApp(App[None]):
    """KamaClaude TUI：终端滚屏风格，实时展示 agent 执行过程。"""

    TITLE = "KamaClaude"
    BINDINGS = [
        Binding("q", "quit", "quit"),
    ]
    CSS = """
    Screen { background: $background; }
    #header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #log-view {
        height: 1fr;
    }
    Static.run-header { color: cyan; padding: 1 2 0 2; }
    Static.step-divider { color: $text-muted; padding: 0 2; }
    Static.run-ok { color: green; padding: 0 2 1 2; }
    Static.run-err { color: red; padding: 0 2 1 2; }
    Static.usage { padding: 0 2; }
    Static.log-line { padding: 0 2; }
    """

    # 初始化连接参数和 TUI 内部状态
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        # super 调用 Textual 父类初始化，不能只初始化自己的连接参数。
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._client: SocketClient | None = None
        self._current_llm: LLMStreamBlock | None = None
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}

    # Textual 调用的生成器：yield 逐个交出子控件；不是一次性返回一个列表。
    def compose(self) -> ComposeResult:
        yield Label("[bold]KamaClaude[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")

    # 控件挂载后启动收消息 worker，让界面事件循环继续处理输入与重绘。
    def on_mount(self) -> None:
        # worker 把长期收消息的协程交给框架管理，避免 on_mount 永远不能返回。
        # exclusive 会取消同组已有 worker；退出应用时框架负责取消 worker。
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")

    # 向日志视图追加一个 widget 并滚动到底部
    def _append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    # 结束当前 LLM 流式块（下一个 token 将开启新块）
    def _break_llm(self) -> None:
        self._current_llm = None

    # 管理 SocketClient 生命周期：连接、订阅事件、断线重连
    async def _socket_loop(self) -> None:
        # 步骤 1：Textual query_one 找到 compose 创建的顶部状态控件。
        header = self.query_one("#header", Label)

        # 步骤 2：每轮创建新客户端；连接失败则等待重试。
        # 外层循环管理重连；每轮新建客户端，避免复用已关闭的连接和 pending 状态。
        while True:
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                header.update("[bold]KamaClaude[/bold]  [red]not connected — retrying...[/red]")
                await asyncio.sleep(2)
                continue

            # 步骤 3：显示连接状态并启动读循环，再发送订阅命令才能有人接收响应。
            self._client = client
            header.update(
                f"[bold]KamaClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            )
            loop_task = asyncio.create_task(client.run_event_loop())

            # 步骤 4：将网络事件交给界面显示方法。
            # 将 SocketClient 解码后的普通 dict 交给同步渲染函数。
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            client.on_event(on_event)

            # 步骤 5：订阅事件，并按需附带历史回放请求。
            try:
                params: dict[str, Any] = {
                    "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage", "log.*"],
                    "scope": "global",
                }
                # 重连时仍会请求同一历史回放；当前没有保存消费游标或去重逻辑。
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                await client.send_command("event.subscribe", params)
                # 正常断线会结束接收任务；随后 finally 关闭客户端并进入重连。
                await loop_task
            except IpcError as e:
                header.update(f"[bold]KamaClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                # 步骤 6：断线/取消时关连接并结束当前文本块；已显示的历史控件保留。
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._break_llm()
                await client.close()

            # 步骤 7：异步等待两秒再连接；sleep 让出执行权，不会阻塞界面重绘。
            header.update("[bold]KamaClaude[/bold]  [dim]disconnected — retrying...[/dim]")
            await asyncio.sleep(2)

    # 根据事件 type 路由到对应渲染逻辑
    def _handle_event(self, event: dict[str, Any]) -> None:
        # 步骤 1：读取类型标签；未知类型最终静默忽略。
        t = event.get("type", "")

        # 步骤 2：连续 token 更新同一个 LLMStreamBlock，每次追加立即更新显示。
        if t == "llm.token":
            token = event.get("token", "")
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            self._current_llm.append_token(token)
            return

        # 步骤 3：任何非 token 事件都会切断连续块，下一段文本另起控件。
        self._break_llm()

        # 步骤 4：按事件类型选择显示格式并写入日志控件。
        if t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[bold cyan]▶ run[/bold cyan]  [dim]{run_id}[/dim]\n"
                f"  [dim]goal:[/dim] {goal}",
                classes="run-header",
            ))

        elif t == "step.started":
            step = event.get("step", "")
            self._append(Static(
                f"[dim]── step {step} {'─' * 48}[/dim]",
                classes="step-divider",
            ))

        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            tc_block = ToolCallBlock(tool_name, params)
            # 按调用 ID 匹配结束事件，同一工具名可同时出现多次，不能用名字做键。
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                # dict.pop 同时取得并移除记录，避免已完成块继续占用待处理索引。
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                # dict.pop 同时取得并移除记录，避免已完成块继续占用待处理索引。
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        elif t == "run.finished":
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            if status == "success":
                self._append(Static(
                    f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                    classes="run-ok",
                ))
            else:
                detail = f"  [dim]{reason}[/dim]" if reason else ""
                self._append(Static(
                    f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                    classes="run-err",
                ))

        elif t == "llm.usage":
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]",
                classes="usage",
            ))

        elif t == "log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


# TUI 入口：读取配置并启动 KamaTuiApp
def run(config: KamaConfig, replay_run_id: str | None = None) -> None:
    app = KamaTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    app.run()
