# study/s1 源码阅读导航

本分支以 `origin/stage/s1` 为基础，保留 S0 教学注释，并补充 S1 的执行循环、模型适配、工具调用与事件记录注释。Windows 信号处理和 pytest 临时目录修复已移植。

## 从 S0 到 S1

S0 的 `kama ping` 通过 TCP 请求独立运行的 `kama-core`。S1 新增的 `kama run --goal "..."` 则直接在 CLI 进程内调用 AgentRunner，不需要先启动 core。此时 core 仍只注册 ping，TUI 仍是占位入口。

```text
cli.main → cmd_run → AgentRunner.run
                       ├─ 创建 ExecutionContext：目标、消息、步数、状态
                       ├─ 创建 EventBus：订阅终端打印器和 EventWriter
                       ├─ 创建 Provider 和 ToolRegistry：注册 read_file
                       └─ AgentLoop.run
                            ├─ Provider.chat：请求模型并接收流式响应
                            ├─ 把文本和 tool_use 放入消息历史
                            ├─ invoke_tool：本地执行工具，结果追加到历史
                            └─ 继续请求模型，直到 end_turn 或达到步数上限
```

事件是这条执行链的观察记录：总线把事件交给终端打印器和文件写入器；它本身不会执行工具。工具结果通过消息历史反馈给模型。

## 建议阅读顺序

| 顺序 | 文件 | 重点 |
|---|---|---|
| 1 | [CLI run](../src/kama_claude/cli/commands/run.py) | 同步入口如何调用异步 Runner？打印器如何订阅事件？ |
| 2 | [Runner](../src/kama_claude/core/runner.py) | 依赖注入、关键字参数、async with、任务取消收尾。 |
| 3 | [Context](../src/kama_claude/core/context.py) | dataclass、default_factory、列表和字典引用、短路判断。 |
| 4 | [AgentLoop](../src/kama_claude/core/loop.py) | 一步的含义、工具结果反馈、成功与步数限制的优先级。 |
| 5 | [模型类型](../src/kama_claude/core/llm/types.py)与[接口](../src/kama_claude/core/llm/base.py) | 内部数据对象与 Protocol 的作用。 |
| 6 | [模型适配器](../src/kama_claude/core/llm/provider.py) | **kwargs、async for、getattr、流式文本和完整响应。 |
| 7 | [工具契约](../src/kama_claude/core/tools/base.py)与[注册表](../src/kama_claude/core/tools/registry.py) | ABC、abstractmethod、实例与 schema 的区别。 |
| 8 | [调用入口](../src/kama_claude/core/tools/invocation.py)与[读文件工具](../src/kama_claude/core/tools/builtin/read_file.py) | cast、列表推导式、超时、错误转换、字节截断。 |
| 9 | [事件模型](../src/kama_claude/core/bus/events.py)、[总线](../src/kama_claude/core/events/bus.py)、[写入器](../src/kama_claude/core/events/writer.py) | 判别联合、异步回调、JSONL、资源关闭。 |
| 10 | [循环测试](../tests/unit/test_loop.py)、[适配器测试](../tests/unit/test_llm_provider.py)、[Runner 测试](../tests/unit/test_runner.py) | 用假对象验证真实业务逻辑，无需请求 API。 |

[S0 导航](STUDY_GUIDE_S0.md)保留作历史学习材料，其中“只有一个事件模型”“尚未记录运行”等描述仅适用于 S0。当前 S1 以本导航和对应源码为准。

## 一次工具调用怎样完成

以“读取某个文件”为例，模型可以返回 `ToolCallBlock(id=..., name="read_file", input={"path": ...})`。这是调用请求，不是执行结果。

1. Loop 将这次请求记录为 assistant 消息中的 `tool_use`。
2. invoke_tool 按 name 找工具，检查必填参数，再等待工具执行。
3. Context 把结果包装为 user 消息中的 `tool_result`，用 tool_use_id 关联原调用。
4. 下一次 chat 接收包含工具结果的完整历史，模型据此继续回答。

这里的 user 消息不一定来自人的输入，也可能是程序构造的工具反馈。工具失败同样进入历史；模型调用异常则直接把任务标记为失败。

## 容易误读的实现细节

- Protocol 用于描述接口；不要求 AnthropicProvider 显式继承它。普通 dataclass 的字段标注不负责运行时校验。
- EventBus 按订阅顺序逐个 await，同步传递同一个事件对象；没有后台队列，也没有自动隔离订阅者异常。
- `async def` 不保证内部操作非阻塞。当前文件读取、事件文件写入仍是同步调用。
- invoke_tool 只校验必填键是否存在，没有完整验证 JSON Schema；cast 也不执行数据转换。
- ReadFileTool 拒绝路径组成部分中的 `..`，但允许绝对路径，也未限制符号链接目标。工具说明中的“相对路径”不是完整实现的边界检查。
- 512 KB 是返回内容上限；实现先读取整个文件，再截断字节并解码。
- `llm.token` 事件携带流式文本片段，不保证每个片段恰好对应一个模型 token。
- 模型异常或取消可以提前离开当前步骤，不保证每个 step.started 都有对应的 step.finished。
- 模型名配置传给 Anthropic SDK；S1 没有根据模型名前缀自动切换供应商的路由实现。

## 在 Windows 上练习

```powershell
uv sync
uv run pytest tests/unit -v
uv run pytest tests/integration/test_ping_roundtrip.py -v
uv run ruff check src tests scripts
uv run mypy src
uv run python scripts/gen_protocol_doc.py --check
```

先读假 provider 测试并设置断点观察 messages 的变化，再尝试真实运行。`tests/integration/test_run_e2e.py` 会读取 `.env`，有凭据时调用真实 Anthropic API；因此上面的离线验证没有包含它。

真实 `kama run` 需要有效的 `ANTHROPIC_API_KEY` 和该服务可用的模型配置。运行事件写到当前工作目录的 `runs/<run_id>/events.jsonl`。S0 的 Windows 退出修复用于 daemon；run 命令仍由 asyncio.run 与 KeyboardInterrupt 处理 Ctrl+C。
