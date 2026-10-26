# S7 来源、合并与本地验证记录

日期：2026-10-26。执行范围：本地 `study/s7`，不推送、不调用真实模型、不读取真实 `.env` 或 API Key。未使用子代理；源码中的子 Agent 仅由 fake Provider 测试驱动。

## 1. 仓库基线与官方来源

- 初始为 `study/s6`，准确提交 `1ce99c8591aab0f18fa464d3c9f5050c6db2ee94`；没有已跟踪或可见未跟踪的用户修改，`study/s7` 不存在。两个原有不可读 `pytest-cache-files-*` 目录原样保留，未删除或移动。
- 阅读根 `AGENT.md`、`CLAUDE.md`，检查 W:/、W:/Code、W:/Code/GitHubProject 上级说明和 src/tests/scripts/doc 嵌套说明，没有发现额外适用文件。完整阅读 S6 指南（含第 8 节）与验证记录。
- `origin` 是 `https://github.com/x-wynter/KamaClaude.git`。已有 `official/stage/s6` 只是引用，不存在 official remote 配置；不能把 origin 或引用名字本身当作来源证明。
- 对此前确认的官方地址 `https://github.com/youngyangyang04/KamaClaude.git` 直接执行 `git ls-remote`，再直接 fetch 官方 S6/S7 到 `refs/remotes/official/stage/*`。获取对象与现有 S6/S7 阶段引用一致，没有改 origin 或添加推送配置。
- 官方 S6：`f4567c1781198de6807855c6a55879f5b8704c8e`。
- 官方 S7：`70decaf06b284b8e72b90f8369ec75bbfcf4dc81`。
- S6→S7 的 5 条提交依次为 `d9544f1`（子 Agent/Skills/MCP）、`68922eb`（thinking）、`63f922e`（目录式 Skill/块标量）、`b017168`（MCP 与传输修复）、`70decaf`（TUI banner）。提交号来自本次实际 Git 查询。
- 执行 `git switch -c study/s7 study/s6`，随后 `git merge --no-commit --no-ff official/stage/s7`。Git 写元数据受沙箱限制，正常本地执行权限通过自动审批后完成。

## 2. 官方差异逐项与学习问题对应

官方 S6→S7 共 **36 文件：22 新增、14 修改、0 删除**。下表列出全部文件；路径前缀 `src/kama_claude/` 按需缩写。Q 对应 [指南第 1 节](STUDY_GUIDE_S7.md) 的 14 个问题。

| 类型 | 官方文件 | 核心问题与验证 |
| --- | --- | --- |
| M | WIRE_PROTOCOL.md | Q12；本地生成器补齐三类新事件后重新生成 |
| A | core/agents/__init__.py | Q5；包导出，非调度入口 |
| A | core/agents/builtin/executor.toml | Q1、5；角色工具与模型未接线说明 |
| A | core/agents/builtin/planner.toml | Q1、5；角色加载单测、TUI 子任务 |
| A | core/agents/builtin/reviewer.toml | Q1、5；bash 白名单边界 |
| A | core/agents/loader.py | Q5；官方角色加载测试 |
| M | core/app.py | Q2、10；启动失败清理、全量 daemon 测试 |
| M | core/bus/events.py | Q12；协议生成及 TUI 事件闭环 |
| M | core/config.py | Q11；全局/项目/显式路径优先级和列表追加回归 |
| M | core/context.py | Q3、7；Skill system 和三层上下文回归 |
| M | core/llm/provider.py | Q13；FakeStream thinking/signature 回放 |
| M | core/llm/types.py | Q13；同上，LlmResponse 携带块 |
| M | core/loop.py | Q3、13；子 Loop、thinking 保存及旧工具链测试 |
| A | core/mcp/__init__.py | Q8；包导出说明 |
| A | core/mcp/client.py | Q8、9、10；本机 TCP/stdio、字符串 ID、大响应、isError、回收 |
| A | core/mcp/server.py | Q2、8、10；握手/发现/取消失败清理 |
| A | core/mcp/tool.py | Q8、9；官方适配器错误映射单测 |
| M | core/runner.py | Q2、4、7、10；后台取消/正常收尾、下一 run；S6 摘要回归 |
| M | core/session/manager.py | Q6、7；Skill 实际输入、裸斜杠、会话锁及后续消息 |
| A | core/skills/__init__.py | Q6；包导出说明 |
| A | core/skills/builtin/init.md | Q6；指南说明生成项目 context 的提示用途 |
| A | core/skills/builtin/orchestrate.md | Q1、6；TUI 真实补全/审批/子任务链 |
| A | core/skills/builtin/review.md | Q6；加载测试与白名单阅读 |
| A | core/skills/builtin/summarize.md | Q6、7；与 /compact 的区别说明 |
| A | core/skills/loader.py | Q6；官方解析/覆盖测试及主线参数回归 |
| A | core/subagent/__init__.py | Q3；包导出说明 |
| A | core/subagent/registry.py | Q4、10；取消等待和清空表回归 |
| A | core/subagent/tool.py | Q3、4、5、9、10；前后台、深度、状态与失败查询 |
| M | core/transport/socket_client.py | Q12；全量 TCP/TUI 闭环 |
| M | core/transport/socket_server.py | Q10、12；原请求清理与新增 writer 关闭合并 |
| M | tui/app.py | Q6、12；补全/审批/子事件/继续输入及旧 /compact 回归 |
| A | tests/unit/test_agent_profile_loader.py | Q5、14；官方测试保留 |
| A | tests/unit/test_mcp_tool.py | Q8、14；官方测试保留 |
| M | tests/unit/test_session_manager.py | Q6、14；fake Runner 接口新参数 |
| A | tests/unit/test_skill_loader.py | Q6、14；官方测试保留 |
| A | tests/unit/test_spawn_agent_tool.py | Q3、4、5、14；官方测试保留 |

不是每个配置模板都进行真实模型执行；表中“阅读/说明”不计为独立运行验证。没有新增依赖或修改用户配置。

## 3. 合并冲突与保留修复

9 个冲突文件：core/app、config、context、llm/provider、loop、runner、transport/socket_client、transport/socket_server、tui/app。按冲突块阅读两侧处理，没有整文件选择 ours/theirs。

| 冲突点 | 处理结果 |
| --- | --- |
| app | 合入 MCP 装配和关闭；保留跨平台退出、取消等待、断连审批；去掉官方重新引入的急切 compact Provider 构造 |
| config | 采用官方全局→项目 TOML 与 MCP 类型/解析，保留其他校验及中文说明 |
| context / provider / loop | 保留 compaction_count 与记忆，接入 system override 和 thinking/signature；更新旧注释 |
| runner | 工具装配移至 Provider 创建后，支持子工具/MCP/白名单；保留压缩后整体持久化与取消收尾 |
| socket_client | 合入官方 64 MiB 与超长行异常分支，保留既有说明 |
| socket_server | 同时保留请求 Task 跟踪/取消等待、断连回调及官方活跃 writer 关闭 |
| TUI | 插入补全、banner、子任务显示；保留权限 worker、S6 compact 和原类型修复 |

S6 的 compaction_count、微秒 thread 备份、手动 Provider 工厂和协议生成登记没有被官方替代，继续保留。官方 S7 对 Server 活跃连接关闭是补充，不替代学习分支请求取消等待。原 Windows、UTF-8、审批 Future 清理、shell 直接子进程回收与流重试均保留。旧文档未改写；根说明仅追加 S7 索引。

## 4. 本次学习分支新增必要修复

1. **Skill 实际执行输入**：把已展开 `$ARGUMENTS` 的正文用作 system override；裸 `/` 不再触发空列表索引。原始输入仍保存到 thread。回归直接检查真实 Provider 参数、工具集合、磁盘与下一轮。
2. **子任务生命周期**：Runner 收尾取消并等待本轮未完成后台任务，清空表；子前后台共用执行体，异常/取消更新 Context 并发完成事件；后台查询将失败 Context 返回为错误。没有实现跨 run 后台服务，父模型必须在本轮取结果。
3. **MCP 错误与回收**：处理 result.isError；连接握手/发现失败或取消时关闭未登记 client；stop_all 清除工具缓存；stdio 强杀后等待进程。Core 将 MCP 启动和监听纳入 finally，监听失败也关闭 MCP/trace。
4. **协议与静态检查**：生成器登记 SkillInvokedEvent/SubagentStartedEvent/SubagentFinishedEvent 并生成完整模型章节；修复官方新增代码的长行/导入格式，banner 文本不变。
5. **回归适配**：S3 测试仍严格比较完整工具集合，新增两个 S7 工具；S6 TUI 测试先 Esc 收起补全再提交 compact，原备份/摘要/续聊断言不变。没有删除测试或放宽断言掩盖问题。

没有实施 S6 正式历史原子保存、恢复/续聊拦截、摘要质量校验等重构。S7 现存边界逐项记录于指南第 8 节。

## 5. 实际验证命令与结果

使用现有 Python 3.12 `.venv`，没有安装/升级依赖；排除真实模型 `test_run_e2e.py` 收集。Fake Provider、临时 HOME/cwd 和环境隔离沿用仓库夹具；新增 MCP 服务仅本机临时 TCP/Python 子进程。没有运行真实外部 MCP 服务。

```powershell
# 首次沙箱执行（环境失败，保留目录）
.venv/Scripts/python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s7-validation/cache --basetemp=runs/.s7-validation/first --tb=short

# 正常本地权限首轮
.venv/Scripts/python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s7-validation/cache --basetemp=runs/.s7-validation/local-first --tb=short

# 聚焦首轮与修订后复验
.venv/Scripts/python.exe -m pytest tests/unit/test_s7_regressions.py tests/integration/test_s7_tui_offline.py -q -o cache_dir=runs/.s7-validation/cache --basetemp=runs/.s7-validation/focused --tb=short
.venv/Scripts/python.exe -m pytest tests/unit/test_s7_regressions.py tests/integration/test_s7_tui_offline.py tests/integration/test_s6_tui_offline.py tests/integration/test_s3_task_graph.py -q -o cache_dir=runs/.s7-validation/cache --basetemp=runs/.s7-validation/focused2 --tb=short

# 最终全量
.venv/Scripts/python.exe -m pytest tests/unit tests/integration --ignore=tests/integration/test_run_e2e.py -q -o cache_dir=runs/.s7-validation/cache --basetemp=runs/.s7-validation/final --tb=short
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/mypy.exe src
.venv/Scripts/python.exe scripts/gen_protocol_doc.py --check
git diff --check
git diff --cached --check
```

| 检查 | 实际结果 |
| --- | --- |
| 沙箱 pytest | Windows 临时目录访问拒绝，环境失败；不计通过，不删除该目录 |
| 正常本地首轮全量 | 310 passed、2 failed，119.27 秒；旧工具集合与 compact 键盘流程不适配 S7 |
| 新增聚焦首轮 | 9 passed、1 failed，19.90 秒；TUI spawn 审批尚未操作，测试等待超时 |
| 修订后聚焦 | 17 passed，32.85 秒；加入真实键盘批准和新补全步骤 |
| 最终全量 | **325 passed，114.13 秒**，包含后补的 stdio 与 Core 启动失败测试 |
| Ruff | All checks passed；初轮发现 14 个格式问题后修正 |
| Mypy | Success，85 source files |
| 协议同步 | --check 通过；三类新事件已登记，不只比较旧生成结果 |
| 注释行为 | 85 个源码 AST 与补注释前一致 |
| Markdown | 两份新文档链接存在、表格列数一致、围栏闭合；指南 1/8 节均为 14 题连续编号 |
| 旧学习文档 | 按 Git LF 规范化比较，原 doc 文件内容不变 |
| Git 空白检查 | 工作区和暂存区 diff --check 通过 |

执行输出保留在被忽略的 `runs/.s7-validation/*.log`，不提交临时缓存/测试目录。AST 快照为该目录下 `ast-before-comments.json`。Markdown/AST/旧文档检查用 Python 标准库内联脚本完成；未声称用浏览器渲染 Mermaid 或进行截图 QA。

## 6. 关键行为证据与未覆盖事项

- Skill：真实 Manager→Runner→Provider 检查参数展开、记忆、白名单、原始磁盘输入和裸斜杠续聊。
- 子任务：真实子 Loop 的前后台、失败状态、深度；父正常结束/取消后 Task 已完成取消、Context 为 failed、注册表清空、子文件存在、父日志有完成事件、会话锁释放、下一 run 可保存新历史。
- thinking：SDK FakeStream→Provider→Loop→thread→下一请求，逐字段检查原 thinking/signature。
- MCP：真实本机 TCP 握手、字符串 ID、通知/噪声跳过、超过 64 KiB 响应、isError；真实 stdio 子进程回收和 stderr task 归零；模拟握手/发现/取消失败验证 close；模拟 Core 监听失败检查 MCP/trace 清理。
- TUI：真实 Textual 键盘补全→同连接审批→Core Skill→子 Loop→父工具结果→磁盘事件和历史→继续对话；未仅验证孤立控件。
- 保留全量 S6 记忆、截断、自动摘要跨 run、手动压缩取消/失败/备份、流重试、max_tokens 配对，S5 参数校验、审批、工具重试、断连、取消和会话测试。

未验证真实模型质量/计费环境、真实网络中断、第三方 MCP 兼容、MCP 重名配置、完整 IPC 64 MiB 超限恢复、任意文件写失败恢复、所有并发取消时序以及整棵子进程树回收。现有非原子 thread 写入仍可能损坏下一轮上下文，不能据 325 个离线测试通过宣称恢复机制已实现。

## 7. 本地提交

本次以合并提交保存，双父分别为本节记录的 S6 学习基线和官方 S7。提交题为 `feat(study): complete S7 learning merge and offline validation`。记录位于提交内部，不嵌入自身哈希；准确提交与父关系可通过 `git log -1 --format='%H %P %s'` 查询。完成后留在 `study/s7`，不推送远程。
