# ═══ 文件职责：查看统一 trace 时间线 ═══
# 协作关系：cli/main 解析参数 → cmd_trace 读文件 → TraceRecord 校验 → 摘要或原始行。
# 阅读路线：cmd_trace → _process_line → _print_record/_summarize。
# trace 只观察已写出的记录，不重放工具或恢复模型任务。

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from kama_claude.core.config import KamaConfig
from kama_claude.core.trace.record import TraceRecord

_COLORS = {
    "CLIENT→CORE": "\033[36m",
    "CORE→CLIENT": "\033[33m",
    "CORE":             "\033[32m",
    "CORE→LLM":   "\033[35m",
    "LLM→CORE":   "\033[34m",
}
_RESET = "\033[0m"
_BOLD = "\033[1m"


# kama trace 子命令：从 daemon.jsonl 读取并展示 trace 记录
def cmd_trace(
    run_id: str | None,
    config: KamaConfig,
    *,
    layer: str | None = None,
    direction: str | None = None,
    raw: bool = False,
    follow: bool = False,
) -> None:
    # 步骤 1：标准库 Path.expanduser 展开 ~；trace 是本地文件读取，不连接 daemon。
    trace_path = Path(config.trace.file).expanduser()
    if not trace_path.exists():
        print(f"trace file not found: {trace_path}", file=sys.stderr)
        sys.exit(1)

    # 步骤 2：逐行读取字节；兼容旧 Windows 日志与新 UTF-8 日志混合追加。
    with open(trace_path, "rb") as f:
        for line in f:
            _process_line(
                _decode_line(line).strip(),
                run_id=run_id,
                layer=layer,
                direction=direction,
                raw=raw,
            )

    # 步骤 3：follow 重新打开并移到文件末尾，之后轮询追加内容。
    # 两次打开之间新增的行可能跳过；当前没有文件轮转处理或消费游标。
    if follow:
        with open(trace_path, "rb") as f:
            # seek 的 whence=2 表示文件末尾；readline 在暂无新内容时返回空字节串。
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    _process_line(
                        _decode_line(line).strip(),
                        run_id=run_id,
                        layer=layer,
                        direction=direction,
                        raw=raw,
                    )
                else:
                    # 标准库 sleep 同步阻塞 50ms；本 CLI 没有运行 asyncio 事件循环。
                    time.sleep(0.05)


# 优先解码 UTF-8，失败时兼容旧 Windows GBK/GB18030；损坏行提示后跳过。
def _decode_line(line: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return line.decode(encoding)
        except UnicodeDecodeError:
            continue
    print("warning: skipping undecodable trace line", file=sys.stderr)
    return ""


# 解析单行并根据过滤条件决定是否输出
def _process_line(
    line: str,
    *,
    run_id: str | None,
    layer: str | None,
    direction: str | None,
    raw: bool,
) -> None:
    # 步骤 1：跳过空行/无法校验的记录；Pydantic model_validate 得到有属性的模型。
    if not line:
        return
    try:
        record = TraceRecord.model_validate(json.loads(line))
    except Exception:
        return

    # 步骤 2：各条件同时成立才输出；run_id 过滤检查记录顶层，不递归查 data。
    if run_id is not None and record.run_id != run_id:
        return
    if layer is not None and record.layer != layer:
        return
    if direction is not None and record.direction != direction:
        return

    # 步骤 3：raw 保留原 JSON 行；默认调用项目 _print_record 输出摘要。
    if raw:
        print(line)
    else:
        _print_record(record)


# 将单条 TraceRecord 格式化为彩色单行输出
def _print_record(record: TraceRecord) -> None:
    color = _COLORS.get(record.direction, "")
    ts = record.ts[11:23] if len(record.ts) >= 23 else record.ts

    # f-string 的 :<14 表示左对齐、最小宽度 14；ANSI 转义序列给终端设置颜色。
    direction_str = f"{color}{_BOLD}{record.direction:<14}{_RESET}"
    kind_str = f"{record.kind:<13}"

    parts: list[str] = []
    if record.run_id:
        parts.append(f"run={record.run_id[:8]}")
    if record.step is not None:
        parts.append(f"step={record.step}")
    parts.append(_summarize(record))

    print(f"{ts}  {direction_str}  {kind_str}  {'  '.join(parts)}")


# 从 data 字段提取关键摘要（不输出大型 payload）
def _summarize(record: TraceRecord) -> str:
    data = record.data
    kind = record.kind

    if kind == "command":
        params = data.get("params", {})
        goal = str(params.get("goal", ""))
        suffix = f'  goal="{goal[:50]}"' if goal else ""
        return f"method={data.get('method')}{suffix}"

    if kind == "response":
        result = data.get("result", {})
        if isinstance(result, dict) and "run_id" in result:
            return f"run_id={result['run_id'][:8]}"
        return str(result)[:60]

    if kind == "error":
        err = data.get("error", {})
        return f"code={err.get('code')}  {err.get('message', '')}"

    if kind == "push":
        return f"event={data.get('event_type')}  sub={data.get('sub_id')}"

    if kind == "event":
        return f"type={data.get('type')}"

    if kind == "api_call":
        msgs = data.get("messages")
        count = len(msgs) if isinstance(msgs, list) else data.get("message_count", "?")
        tools = data.get("tool_schemas")
        tc = len(tools) if isinstance(tools, list) else data.get("tool_count", "?")
        return f"msgs={count}  tools={tc}"

    if kind == "api_error":
        return f"error={data.get('error_type')}  status={data.get('status_code', 'n/a')}"

    if kind == "api_response":
        usage = data.get("usage", {})
        return (
            f"stop={data.get('stop_reason')}  "
            f"latency={data.get('latency_ms')}ms  "
            f"out_tokens={usage.get('output_tokens', '?')}"
        )

    return str(data)[:60]
