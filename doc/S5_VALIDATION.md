# S5 分支与本地验证记录

日期：2026-10-23。目录：`W:\Code\GitHubProject\KamaClaude`。本次不创建 worktree、不使用子代理、不推送。

## 1. 初始状态与来源

- 初始 `study/s4`，HEAD=`41a8888fd0ea9235358919ac91350960b43faf9d`，与 origin/study/s4 左右提交差为 0/0，无未推送提交。
- 初始工作区、暂存区无已发现用户修改。读取仓库 AGENT.md、CLAUDE.md；检查 W:/、W:/Code、W:/Code/GitHubProject 的三种说明文件，未找到；src/tests/scripts/doc 下无额外适用说明。
- `git fetch origin` 成功，刷新 origin 引用；未关闭 TLS 校验。官方 S4=`9ffbcc497dff6244004712514e7e427c6b9c2853`，官方 S5=`7738b8fb91fe9c1db8eda7445ef926acdc392150`，S5 是 S4 的直接子提交。
- S4 学习提交双父为 e1346d6 与 9ffbcc4。核实已有指南、地图、验证记录、结构化注释、Windows/trace/生命周期与 Textual Pilot 回归；未重复 cherry-pick 旧成果。
- 两个根目录 pytest-cache-files-* 仍不可读；保留原状，不删除、不移动。检查范围为 Git 跟踪文件与 src/tests/doc/scripts，不能声称审查了缓存内部。

## 2. 分支与合并

从完整 study/s4 创建 study/s5，执行 `git merge --no-commit --no-ff origin/stage/s5`。初次写 .git 被沙箱权限拒绝，获准本地执行后成功创建分支并进入合并。使用两侧历史的合并提交，不覆盖或重置分支。

12 个文件发生内容冲突：core/app、bus/events、config、loop、五个 builtin 工具、invocation、socket_server、tui/app。逐个检查冲突块：采用 S5 参数模型、error_class、权限入口、并发请求和界面逻辑；保留适用旧注释、Windows 信号/UTF-8/取消收尾。未用整文件 ours/theirs 覆盖。官方 S5 新增文件及非冲突变更全部保留。

本次合并提交题为 `feat(study): merge S5 permissions with Chinese learning guides and offline regressions`，双父应为 41a8888 和 7738b8f。记录本身在提交内，不嵌入循环依赖的自身哈希；准确哈希可用 `git log -1 --format=fuller` 查看。提交后不推送。

## 3. 相对官方 S5 的执行差异

官方 69 个 Python 源文件全部保留。最终 AST 对比：58 个与官方一致，11 个存在以下差异；不把注释差异算成执行差异。

| 文件 | 额外差异与理由 |
| --- | --- |
| cli/commands/core.py | 继承 Windows PID 存活判断、无窗口后台启动、PID UTF-8。 |
| cli/commands/trace.py | 继承按行 UTF-8/GB18030 混合读取与 api_error 摘要。 |
| core/app.py | 继承跨平台信号恢复、UTF-8 回放与 one_shot/chat 取消等待；新增按连接记录 session，断连/close 拒绝已有审批。 |
| core/config.py | 给环境超时的 float 使用独立变量，修复官方复用 int 变量导致的 Mypy 错误。 |
| core/permissions/manager.py | 审批登记后的广播与等待纳入 try/finally；取消、广播异常、正常、超时均删除 pending 并取消未完成 Future；等价 TimeoutError 拼写满足 Ruff。 |
| core/task/manager.py | 继承任务 JSON 显式 UTF-8。 |
| core/tools/builtin/bash.py | 取消时 kill 并 communicate 等待直接子进程，防止外层超时/Core 退出遗留进程。 |
| core/trace/provider.py | 继承 api_error 诊断后原异常传播，保持官方 system 参数透传。 |
| core/trace/writer.py | 继承 UTF-8 追加。 |
| core/transport/socket_server.py | 保留 S5 并发请求；保存任务强引用、读取异常，stop 时取消并等待；断连回调连接到 Core 审批清理。 |
| tui/app.py | 修复官方导入位置、排序、引号类型标注与局部变量类型复用；保留官方审批功能和 worker。 |

其余非执行差异：中文分层注释与文档；官方超长行格式修正；测试中删除官方未用 import；新增隔离夹具和 fake 写文件场景、TUI/清理/断连回归。保留全部历史有效测试。pyproject/lock 的变化只有官方 S5 增加 textual-dev 开发依赖，没有本次额外依赖调整；实际复用现有 .venv，未安装或升级。

## 4. 注释与静态完整性

在功能适配、静态修正后，保存 69 文件 AST 基线到忽略目录 runs/.s5-validation/ast-before-comments.json；随后仅整理注释。最终 69/69 一致（忽略位置属性）；功能适配与注释分开核验。旧 S0～S4 指南和旧 S3/S4 验证记录与初始 study/s4 逐一比较不变。

检查源文件顶部职责/协作/阅读路线，新增权限类字段与 Future，复杂函数分步骤。更新旧工具“仅必填检查”、旧 socket 串行解释、bash 无审批等过时说明；保留类型、回调、队列、锁与上下文管理的教学说明。

## 5. 实际命令与结果

```powershell
.venv\Scripts\python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s5-validation/cache --basetemp=runs/.s5-validation/final --tb=short
.venv\Scripts\ruff.exe check src tests scripts
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe scripts/gen_protocol_doc.py --check
.venv\Scripts\python.exe runs/.s5-validation/check_docs.py
node runs/.s5-validation/check_mermaid.mjs
git diff --check
```

| 检查 | 结果 |
| --- | --- |
| 首轮沙箱测试 | Windows basetemp 访问拒绝，属于环境失败，不计通过。未删除遗留缓存。 |
| 有效首轮全量 | 250 passed，85.92 秒。 |
| 新增 TUI 与清理聚焦 | 7 passed，13.00 秒。 |
| 最终全量 | 258 passed，102.19 秒。 |
| Ruff | 最终复核通过。 |
| Mypy | 69 个源码文件通过。 |
| 协议同步 | gen_protocol_doc.py --check 通过，保持官方 S5 文档。 |
| AST 注释比较 | 69/69 与功能适配基线一致。 |
| 文档静态检查 | 139 个链接、15 个代码围栏、31 个表格、70 个源码索引通过；7 张 Mermaid 实际 parse 通过；5 段 PowerShell 用系统 Parser 静态解析通过。 |

测试显式 --ignore 真实模型模块，防止收集时加载项目 .env。新增 autouse 夹具隔离 HOME/USERPROFILE、KAMA/ANTHROPIC 环境，恢复 dotenv 在单个测试补入的值；不输出密钥。子进程临时 cwd、假 Provider、临时 config/trace/session/policy。未调用付费模型，也未读项目 .env。

## 6. 覆盖与边界

- Windows：信号原生/回退和处理器恢复、PID 查询、不误终止、无窗口参数、UTF-8 任务/回放/trace、混合旧编码、中文/emoji 与启动轮询；继承且重新运行旧回归。
- 审批：允许、拒绝、always 内存/持久化重载、outside-cwd 优先级、超时、断连、广播异常与取消 pending 清理；fake 工具覆盖分类和退避。
- TUI：真实 Textual run_test/Pilot，100×35 虚拟终端，Enter 提交、y/n 审批，同一真实 TCP 连接上的长 RPC 与回应并行，核实文件和 thread 的 tool_result；旧多行/两轮/关闭回归继续运行。
- 新增真实 TCP 断连用例：收到 ASK 后关连接，检查文件未写、permission.denied 与 run.finished 已保存；说明只拒绝已有等待，run 可以继续完成。
- Core：one_shot/chat 在正常退出或等待异常时统一取消、等待，再停止 server/trace。shell 取消只保证直接子进程，不承诺整个进程树。

文档明确：CLI chat 的长 RPC 使 ASK 无法即时读取键盘；默认 task_* ASK；规则不是 Windows 路径沙箱；tool_use_id 全局碰撞和审批归属未校验；TUI 全局事件、多 run/重连显示、重试结果展示尚有限制；Session.status 与锁/Context 独立；close 忙时仍报错。

## 7. 未执行与保留情况

未执行真实模型 e2e、付费 API、模型回答质量、依赖重装、人工真实终端验收或所有尺寸的视觉检查。Mermaid parse 只证明语法，不等于视觉布局验收。未检查不可读历史缓存内部。官方 trace_permission_flow.py 属于手动演示脚本，本次未运行。

初始无用户未提交修改，未 stash、reset、删除或覆盖用户文件；旧指南保留。只提交本次合并、适配、注释、文档与测试；运行日志和 AST 辅助文件留在 Git 忽略的 runs 下，不混入提交。提交前检查暂存差异、冲突标记和空白错误；提交后复核分支与剩余修改。无 push。
