# S3 分支与验证记录

验证日期：2026-10-13。工作目录：`W:\Code\GitHubProject\KamaClaude`。

## 1. 初始状态与合并依据

- 初始分支：`codex/structure-s2-comments`，HEAD=`7ddaf1713e1105747c9a31ecd7344f3eed0befd8`。
- `study/s2` 停在 `f768bfc`，少了 `7ddaf17` 的结构化注释，因此直接从最新 S2 注释分支创建 `study/s3`。
- 执行 `git fetch origin` 后，官方 `origin/stage/s3` 为 `16bbe3b`；相对 S2 的两个提交是 `2619e12`（统一 trace）和 `16bbe3b`（任务内化、八工具、TUI）。
- 双方共同祖先为 `1d4d6ba`（官方 S2），采用 `git merge --no-commit --no-ff origin/stage/s3`，保留两侧历史。
- 逐块处理 10 个冲突文件：CLI main、Core app、envelope、config、runner、builtin 导出、广播器、SocketServer、TUI app 和 TUI 测试。没有用整文件 ours/theirs 覆盖来解决冲突。
- 仓库及检查到的上级目录没有适用 AGENTS.md；读取并遵守现有 CLAUDE.md 的检查约定，注释深度以本次用户要求为准。同步修正 CLAUDE.md 中过时的 Unix socket、仅 ping 等架构描述。
- 初始未提交修改只有 `.gitignore` 末尾增加换行；未编辑、未暂存、未提交该修改。
- 全部操作在当前目录完成；未创建其他 checkout/worktree；未推送远程。

合并与源码提交：`9b7cb75`（父提交为完整 S2 学习分支与官方 S3）。学习指南和本记录在后续独立文档提交中交付。

## 2. 官方功能完整性与额外逻辑

官方 S3 的全部 57 个 Python 源文件均保留。逐文件 `ast.parse` / `ast.dump` 对照官方：52 个 AST 完全相同，只有以下 5 个文件存在执行结构差异。

| 文件 | 相对官方的逻辑差异 | 核验 |
|---|---|---|
| `src/kama_claude/cli/commands/core.py` | 继承 Windows PID 查询、无窗口后台启动、PID 文件 UTF-8 | AST 与 `7ddaf17` 完全一致 |
| `src/kama_claude/core/app.py` | 继承跨平台信号回退/恢复及 UTF-8 回放；finally 内取消全部 S3 run、等待收尾、停服务器与 trace | S2 信号/回放函数 AST 相同；S3 初始化与各 handler AST 与官方相同；生命周期回归通过 |
| `src/kama_claude/core/task/manager.py` | 所有任务 JSON 读写明确 UTF-8 | 去除新增 encoding 关键字后 AST 与官方完全相同 |
| `src/kama_claude/core/trace/writer.py` | trace 追加写入明确 UTF-8 | 同上 |
| `src/kama_claude/cli/commands/trace.py` | trace 历史与 follow 读取明确 UTF-8 | 同上 |

此外，在开始注释整理前保存合并适配后的 57 文件 AST 基线，完成注释后再次比较，全部相同。这区分了有意的兼容修复与纯注释，不将修复误称为“只有注释”。导入顺序调整在基线前完成。

官方八工具、任务清理机制、trace 三层采集、TUI 控件和新增 RunOutcome/result 均保留。没有新增 `task.*` RPC、自动调度、审批或记忆功能。S0/S1/S2 学习指南与起始版本内容一致（比较时归一化 Git LF 与工作区 CRLF）。

## 3. 测试适配原因

| 修改 | 原因与覆盖 |
|---|---|
| 保留旧 Windows 回归 | 信号、PID、隐藏窗口、中文回放仍适用 |
| `test_s3_windows.py` | 中文/emoji task 创建、重载、list、完成清依赖；trace 写入后 CLI raw 读取 |
| `test_app_shutdown.py` 新增参数化用例 | 两个活跃 run 的正常退出与等待异常；确认先回收 run，再停 server/trace |
| bash 超时用当前 Python 解释器 | Windows 不保证有 POSIX sleep；Windows/POSIX 分别采用对应命令行转义 |
| trace 旧测试显式 UTF-8 读取 | 官方测试用默认代码页读取方向箭头，Windows 上可能乱码 |
| `tests/offline_daemon.py` 与共享夹具 | 保留真实子进程/TCP/Runner，只替换模型；临时 cwd/config/trace，不加载项目 .env，不写真实 run 文件 |
| 重写过时 `test_s3_task_graph.py` | 官方文件测试不存在的 task.create/start/cancel RPC。现验证真实 Runner/Loop/四任务工具的数据回填、八工具注册、输出事件、trace，以及 4 个旧 RPC 返回 -32601 |

测试临时目录策略与 10 秒端口就绪上限保留。离线套件直接 ignore 真实 e2e 模块，避免收集该模块时加载 .env；未输出密钥。

## 4. 实际完成的检查

| 检查 | 结果 |
|---|---|
| 全部离线 unit + integration | **171 passed，58.70 秒** |
| Ruff：`ruff check src tests scripts` | 通过 |
| Mypy：`mypy src` | 通过，57 个源码文件 |
| 协议生成：`gen_protocol_doc.py --check` | 通过；WIRE_PROTOCOL.md 与最终模型同步 |
| 纯注释 AST | 57 文件均与注释前基线相同 |
| 对官方源文件完整性与 AST | 无缺失文件，仅上表 5 个有意差异 |
| S0～S2 文档保留 | 内容一致 |
| CLI trace/TUI 参数 | 实际运行 --help，与指南命令匹配 |
| Git whitespace/conflict 检查 | 无遗留冲突标记或空白错误；CRLF 提示只是 Git 换行提示 |
| Markdown 与示例 | 最终结构检查见下方说明 |

实际完整测试命令（使用当前已安装虚拟环境）：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s3-validation/pytest-cache --basetemp=runs/.s3-validation/pytest-local-2
.venv\Scripts\ruff.exe check src tests scripts
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe scripts/gen_protocol_doc.py --check
```

首次沙箱运行受到 Windows 临时目录/子进程权限限制，没有得到有效套件结果；批准在当前仓库执行本地离线测试后，首轮 168 通过、1 失败，定位到 trace 测试默认编码。同步测试读取编码、补充退出回归后得到上面的 171 全通过。未更改真实模型测试来伪造通过。

验证临时文件放在 Git 已忽略的 `runs/.s3-validation/`，未改 .gitignore 或项目 Python 依赖。

## 5. 文档检查与未进行的检查

学习指南采用标准 Markdown 标题目录与相对文件链接，无 HTML 锚点。检查目录目标、源码/测试链接、表格列数、代码块闭合、JSON/TOML/Python 示例语法及 PowerShell 命令语法。图拆为小型流程图和时序图。

实际检查结果：62 个 Markdown 链接、28 个表格、31 个围栏代码块通过结构与示例语法检查；学习指南的 9 个 PowerShell 代码块通过 PowerShell AST 解析。10 张 Mermaid 图全部通过 Mermaid 11.17.2 的真实 parse 校验（Node + jsdom），未声称完成编辑器中的视觉渲染验收。

Mermaid/jsdom 只安装在已忽略的验证目录，使用 --ignore-scripts，未改项目依赖文件。

未执行：

- `tests/integration/test_run_e2e.py` 的真实模型调用：需要凭据且可能收费，按要求未运行。
- 真实模型生成质量、费用、远程 API 连通性。
- 人工操作真实终端 TUI 的点击、滚动、重连与所有尺寸的视觉检查；已有控件单元测试通过。
- 真实 Windows 后台 stop 的优雅退出保证：当前实现使用强制终止，不能保证 finally；前台信号与清理通过受控回归验证。
- 新建干净环境的 `uv sync --frozen`：本次使用已有虚拟环境验证，未重装依赖。

## 6. 学习时应记住的现状

这些是官方 S3 的现有边界，本学习分支在注释和指南说明，未扩展为后续阶段功能：

- TaskManager 记录与清理依赖，不自动调度、不阻止带依赖任务完成，也无事务或环检测。
- Core 能同时受理 run，但共享 EventBus 的 EventWriter 未做 run 过滤/退订，TUI token 也未按 run 分块。
- 工具路径只拒绝 `..`，不是完整沙箱；shell 取消/超时不保证完整进程树回收。
- trace 摘要开关仅影响 LLM 层；队列无上限，写入失败无自动恢复；follow 无游标/轮转保障。
- 模型缺 key 在 Provider 构造时抛 SystemExit，不能视为 Loop 内已统一处理的 llm_error。
