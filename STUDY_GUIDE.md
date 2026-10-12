# study/s0 源码阅读导航

这份导航配合源码中的中文教学注释使用。当前阶段的主要功能是：CLI 向独立运行的 core 进程发送 ping，core 返回版本、运行时长和接收时间。TUI 目前只有占位入口。

## 建议阅读顺序

| 顺序 | 文件 | 要理解的问题 |
|---|---|---|
| 1 | [入口配置](pyproject.toml) | 安装后为什么可以运行 `kama`、`kama-core`？查看 `[project.scripts]`。 |
| 2 | [CLI 入口](src/kama_claude/cli/main.py) | 命令行文字如何变成 `args.version`、`args.command`？ |
| 3 | [配置](src/kama_claude/core/config.py) | 配置对象如何创建？文件和环境变量如何覆盖默认值？ |
| 4 | [日志](src/kama_claude/core/logging_setup.py) | 日志级别、格式和输出位置分别由谁负责？ |
| 5 | [ping 客户端](src/kama_claude/cli/commands/ping.py) | 同步函数如何进入异步代码？字典如何通过 TCP 发送？ |
| 6 | [core 应用](src/kama_claude/core/app.py) | 服务如何启动、注册处理器并响应退出信号？ |
| 7 | [TCP 服务](src/kama_claude/core/transport/socket_server.py) | 一行数据如何变成方法调用，再变成响应？ |
| 8 | [协议外壳](src/kama_claude/core/bus/envelope.py) | 请求、成功响应、错误响应各包含什么字段？ |
| 9 | [命令模型](src/kama_claude/core/bus/commands.py)与[事件模型](src/kama_claude/core/bus/events.py) | Pydantic 如何描述字段和检查数据？哪些定义尚未进入运行链？ |
| 10 | [路径辅助函数](src/kama_claude/core/runs.py) | `Path` 的 `/` 为什么能拼接路径？ |
| 11 | [测试夹具](tests/conftest.py)、[单元测试](tests/unit)、[集成测试](tests/integration/test_ping_roundtrip.py) | 如何准备环境、检查结果、测试异常、清理子进程？ |
| 12 | [文档生成脚本](scripts/gen_protocol_doc.py) | 模型如何转换为 JSON Schema，再拼接为 Markdown？ |

各目录的 `__init__.py` 和 `__main__.py` 也有注释，分别说明包的组织和 `python -m` 的入口机制。版本命令在 [version.py](src/kama_claude/cli/commands/version.py)。

## 跟踪一次 ping

客户端和 core 是两个进程，通常需要先启动 core。客户端并不会自动替你启动服务。

```text
客户端                                      core 进程
cli.main.main()                              app.run()
  ├─ 解析参数                                  └─ asyncio.run(CoreApp().run())
  ├─ get_config()                                  ├─ 配置和日志
  ├─ setup_logging()                               ├─ register("core.ping", 处理器)
  └─ cmd_ping()                                    ├─ SocketServer.start()
       └─ asyncio.run(_ping())                     └─ 等待退出信号
            ├─ 建立 TCP 连接 ───────────────────→ _handle_connection()
            ├─ 发送 JSON + 换行 ────────────────→ _read_loop()
            │                                      └─ _handle_line()
            │                                           ├─ 解析 JSON
            │                                           ├─ 校验请求外壳
            │                                           ├─ 按 method 查处理器
            │                                           ├─ CoreApp._ping_handler()
            └─ 读取响应 ←───────────────────────────────└─ _send()
                 ├─ 校验响应外壳及 PongResult
                 └─ 打印 pong 和耗时
```

`await` 让当前协程等待，并允许同一事件循环调度其他任务；它本身不会创建一个新线程。`asyncio.run()` 是同步入口，调用者会等待整个协程执行完成。

## 配置读取与覆盖是两种顺序

1. `KamaConfig()` 创建默认配置。
2. `load_dotenv(".env", override=False)` 将文件变量补进进程环境，保留已有变量。
3. 从环境读取 `KAMA_CONFIG`，确定 TOML 路径。
4. 将 TOML 中出现的字段应用到配置对象。
5. 将环境中出现的配置应用到对象，然后返回。

因此，`.env` 虽然先读取，其业务配置仍能覆盖 TOML。环境变量的值是字符串，端口需要显式调用 `int()` 转换；普通 dataclass 的类型提示不会自动转换。

## 认出接口属于哪一层

| 类别 | 本项目中的例子 | 阅读重点 |
|---|---|---|
| Python 语法 | `with`、`try/except`、`async/await`、`yield` | 控制执行和资源生命周期，不是普通函数调用。 |
| 内置函数/类型 | `print`、`int`、`isinstance`、`setattr`、`getattr`、`set` | 无需导入；注意参数含义和返回值。 |
| 标准库 | `argparse`、`asyncio`、`json`、`pathlib`、`logging` | 随 Python 提供，但通常要先导入。 |
| 第三方库 | Pydantic、python-dotenv、pytest | 查看类或函数从哪个包导入。 |
| 项目代码 | `get_config`、`SocketServer`、`make_error` | 沿导入路径定位定义，结合调用者理解。 |

最常见的数据转换链为：

```text
Python 字典 → json.dumps → 字符串 → encode → 字节 → TCP
TCP → 字节 → json.loads → Python 数据 → model_validate → 模型对象
模型对象 → model_dump → Python 字典
模型对象 → model_dump_json → JSON 字符串
```

## 阅读时需要区分的现有行为

- 当前使用 TCP，并不是 Unix domain socket。
- `PingCommand` 有模型测试和生成文档用途，但实际 ping 处理器没有先用它校验参数。
- `CoreStartedEvent` 定义了事件数据形状，当前服务启动逻辑没有发送该事件。
- `Command`、`Event` 目前各只有一个模型，并非已投入使用的多成员判别联合。
- 服务端捕获业务校验错误时实际发送 `INVALID_REQUEST`（-32600），并不是常量 `INVALID_PARAMS`（-32602）。
- 超长行分支捕获 `LimitOverrunError`，但 Python 3.12 的 `readline()` 通常将超限转换成 `ValueError`；现有分支不能保证返回预期错误响应。
- 日志的 JSON 模式是字符串模板，没有进行完整 JSON 字符串转义。
- `runs.py` 只计算路径，还没有在 ping 流程中实现运行记录落盘。

这些说明用于准确理解当前源码，本次教学注释不修改上述行为。

## 验证与分支

本次注释采用 `#`，保留现有文档字符串及可执行语句。可通过比较修改前后的 Python AST，检查有没有改变语法结构；再运行已有测试确认当前环境中的行为。AST 相同不代表行号相同，堆栈和调试断点的行号会随注释增加而移动。

项目已有检查命令见 [CLAUDE.md](CLAUDE.md) 和 [Makefile](Makefile)。教学变更应保存在 `study/s0` 的提交中；未提交的工作区改动可能随分支切换一起移动。
