# S4 分支与验证记录

验证日期：2026-10-14。工作目录：`W:\Code\GitHubProject\KamaClaude`。本次在当前目录完成，未创建 worktree，未使用子代理，未推送远程。

## 1. 初始状态与历史依据

- 起始分支 `study/s3`，HEAD=`e1346d6`；工作区与暂存区无已发现修改。
- 检查仓库及 `W:\`、`W:\Code`、`W:\Code\GitHubProject` 上级说明；未发现适用 AGENTS.md，读取仓库 CLAUDE.md。合并后同时阅读官方新增 AGENT.md，修正其过时架构/测试说明。
- 两个 `pytest-cache-files-*` 历史目录仍有访问拒绝警告；没有删除、移动或尝试读取其内容。完整性检查以 Git 跟踪文件及 src/tests/doc/scripts 为范围，不能声明已检查这两个缓存内部。
- 初次 fetch 因沙箱无法写 FETCH_HEAD，授权重试又遇 OpenSSL TLS EOF；使用 `git -c http.sslBackend=schannel fetch origin` 成功刷新引用。
- 刷新后 `origin/study/s3=e1346d6`，本地/远程左右提交差 `0 0`；没有 S3 未推送提交。`origin/stage/s3=16bbe3b`，`origin/stage/s4=9ffbcc4`。
- `merge-base study/s3 origin/stage/s4` 为 `16bbe3b`，官方 S3 到 S4 只有 `9ffbcc4` 这一提交。
- `7ddaf17`、`9b7cb75`、`1d78176`、`940dfac`、`6ed1f46`、`e1346d6` 均为起始 HEAD 祖先；实际文件含结构化注释、Windows 回归、混合 trace 解码、api_error、项目地图和队列注释。没有重复 cherry-pick。

## 2. 分支与合并

执行 `git switch -c study/s4 study/s3`，再 `git merge --no-commit --no-ff origin/stage/s4`。新分支保留两侧历史，不覆盖或重置旧分支。

逐块解决 app/context/llm provider/runner/trace provider/TUI 的内容冲突：会话替代原 run 入口、历史预填、system 指令透传、note_save、TUI 输入采用 S4 实现；保留旧注释中仍准确的部分并更新变化处。trace 冲突同时保留 `system` 透传与 api_error 后原样抛异常。

官方删除的 `test_s3_task_graph.py` 原来测试已不存在的 RPC；本学习分支早已将它改成真实 Loop/任务工具回归，因此保留当前有效版本，并给 fake Provider 补 S4 system 参数。

最终合并提交同时包含本指南、S4 验证记录、项目地图、中文源码注释和回归适配。用 `git log -1 --format=fuller` 查看本记录所在提交；其父提交应为 `e1346d6` 与 `9ffbcc4`。提交后本地 ahead 是本次预期结果，不代表漏推送；本次明确不推送。

## 3. 相对官方 S4 的执行差异

官方 63 个 Python 源文件全部保留。逐文件以 `ast.parse/ast.dump` 比较：**57 个 AST 与官方相同，6 个有以下明确差异**。

| 文件 | 额外逻辑及理由 |
| --- | --- |
| cli/commands/core.py | 继承 Windows PID 查询、隐藏窗口后台启动和 PID 文件 UTF-8，避免错误使用 Unix 进程探测语义。 |
| core/app.py | 继承跨平台信号回退/恢复、UTF-8 回放、finally 中取消并等待 run；S4 chat 同样纳入集合，避免 trace 关闭后才清理 chat。 |
| core/task/manager.py | 继承所有任务 JSON 的显式 UTF-8，保护 Windows 中文/emoji。 |
| core/trace/writer.py | 继承显式 UTF-8 追加写入。 |
| cli/commands/trace.py | 继承按行兼容旧 Windows GB18030 与 UTF-8 混合记录；保留 api_error 摘要显示。 |
| core/trace/provider.py | 继承 api_error 安全诊断与异常继续传播；同时保留官方 S4 system 参数。 |

会话管理、存储、模型循环、Context、note_save、TUI 等 S4 源码 AST 与官方一致。其现有实现边界在指南明确说明，没有悄悄扩展为自动调度、自动压缩或会话恢复。

完成合并适配后保存了 63 文件 AST 基线，再整理注释；最终 **63/63 与基线一致**。功能改动与纯注释不混称。S0～S3 历史指南与起始 e1346d6 内容逐一比较一致。

其他差异：保留早期学习指南和回归测试；新增 S4 指南、更新项目地图与仓库说明；测试 fake 支持 system，daemon 夹具隔离 HOME/USERPROFILE，防止 S4 会话写入真实用户目录；保留临时目录策略与 10 秒启动等待。未修改 Python 依赖或 lock 文件。

## 4. 实际验证命令

使用已有 `.venv`，没有安装或升级项目依赖。以下测试显式跳过真实模型模块，离线 daemon 使用临时 cwd/HOME、fake Provider，不读取项目 .env，不请求付费模型。

```powershell
.venv\Scripts\python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s4-validation/cache --basetemp=runs/.s4-validation/final --tb=short
.venv\Scripts\ruff.exe check src tests scripts
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe scripts/gen_protocol_doc.py --check
.venv\Scripts\python.exe -m kama_claude.cli --help
.venv\Scripts\python.exe -m kama_claude.tui --help
git diff --check
```

## 5. 结果与覆盖

| 检查 | 实际结果 |
| --- | --- |
| 第一轮有效完整离线套件 | 192 passed，62.53 秒 |
| 新增 TUI/退出/trace 聚焦回归 | 15 passed，8.11 秒 |
| 最终完整离线套件 | **196 passed，68.13 秒** |
| Ruff | 全部通过 |
| Mypy | 通过，63 个源码文件 |
| WIRE_PROTOCOL.md | 生成器 --check 通过；官方 S4 模型与生成文档保持同步 |
| 注释前后 AST | 63/63 相同 |
| 官方文件完整性 | 63/63 存在；仅上表 6 文件有逻辑差异 |
| 历史指南 | S0～S3 保持原内容 |
| CLI/TUI --help | 实际运行通过，chat 和 --replay 与指南一致 |

Windows 覆盖包括信号原生/回退与恢复、PID 不误终止、无窗口启动参数、UTF-8 回放/任务/trace、旧编码混合日志、启动等待。生命周期用例覆盖两个 run 的正常/异常退出，并参数化验证 one_shot 与 chat 组合；另测同会话 busy、取消后锁释放及用户历史保留。

TUI 验证使用真实 Textual `run_test`/Pilot：100×35 虚拟终端，Ctrl+J 换行、Enter 提交两轮中文/emoji、busy 恢复、真实 TCP、thread/meta 与两个 run 事件文件、Ctrl+Q 关闭。模型是 fake，界面和协议链路是真实运行。这是自动化验证，不称为人工终端验收。

初次运行因遗漏创建 basetemp 父目录产生临时目录错误，同时静态检查发现一个冲突解析残留的重复初始化方法和超长行，均已修正。第二次沙箱运行遇 Windows 临时目录访问拒绝；随后在授权的本地执行环境中得到有效结果。未删除任何历史拒绝访问目录，也没有把环境失败计作测试通过。

## 6. 文档与静态检查

检查学习指南与项目地图的目录目标、相对源码/测试链接、表格列数、代码围栏、JSON/JSONL/TOML 示例解析，以及全部 src 文件索引覆盖。实际通过：133 个链接、20 个代码围栏、33 个表格、64 个源文件索引（63 Python 加 py.typed）。6 个 PowerShell 示例用系统 Parser.ParseInput 静态解析；9 张 Mermaid 图使用已有 S3 验证目录内的 Mermaid/jsdom 真实 parse，不新增项目依赖。

AST/文档检查脚本与测试日志位于 Git 忽略的 `runs/.s4-validation/`；这些是本地辅助产物，不属于用户源码，也未提交。文档解析、Mermaid parse 属于静态验证，不代表渲染器视觉布局检查；运行命令仅按上表实际执行范围报告。

## 7. 未执行项目与已知边界

- 真实模型 e2e、远程服务连通性、回答质量与费用：需要凭据/网络，可能收费，未运行。
- 新环境 `uv sync --frozen`：本次复用已有虚拟环境，未重装依赖。
- 人工实际终端交互、多终端兼容/全部尺寸视觉检查：完成了自动化 Pilot，未冒称人工检查。
- Windows 后台强制 stop 的优雅退出保证：回归测试覆盖逻辑和参数；操作系统强制结束不保证 finally。
- Mermaid 图实际视觉渲染：只做语法 parse；复杂关系已拆成小图。
- 两个历史 pytest 缓存目录内部：权限不可读，保留原状；不影响跟踪文件审查。

当前业务限制包括内存会话索引不恢复、TUI 重连创建新会话、全局事件隔离不足、同连接长请求延后 close、任务没有自动调度、notes 无自动检索/压缩。指南按实际代码描述，并未把这些边界伪装成已修复。
