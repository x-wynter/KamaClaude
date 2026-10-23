# ═══ 文件职责：执行非交互 shell 命令 ═══
# 协作关系：Runner 注册 → invoke_tool 提供外层超时和事件 → 本工具执行子进程。
# 阅读路线：先看输入 schema，再读 invoke 的启动、超时、解码与退出码分支。
# 审批由外层调用器完成；本类本身没有沙箱，也不保证完整进程树回收。

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from kama_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB
_DEFAULT_TIMEOUT = 60


# BaseModel 校验类型；Field 的 ge/le 限制秒数范围，extra=ignore 忽略多余字段。
class BashParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    command: str
    timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=120)


class BashTool(BaseTool):
    params_model = BashParams
    name = "bash"
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "Non-interactive only — commands requiring user input will hang and time out. "
        "Prefer short, focused commands. Output is truncated at 64 KB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Maximum seconds to wait (default {_DEFAULT_TIMEOUT}, max 120).",
            },
        },
        "required": ["command"],
    }

    # 在子进程中执行 shell 命令，合并 stdout/stderr，超时或非零退出码时返回错误
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 步骤1：解析参数；未指定 executable，Windows 通常使用 cmd.exe，不是 Git Bash。
        p = BashParams.model_validate(params)
        command = p.command
        timeout = p.timeout

        # 步骤 2：标准库 asyncio 启动 shell 子进程；PIPE 捕获输出，STDOUT 合并错误流。
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                # communicate 同时读完管道并等待退出，返回 (stdout, stderr) 字节元组。
                # wait_for 超时会取消等待；此分支再 kill 当前子进程并读取剩余输出。
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            # 外层工具超时和 Core 退出也会取消本协程，先回收直接子进程再传播取消。
            except asyncio.CancelledError:
                if proc.returncode is None:
                    proc.kill()
                await proc.communicate()
                raise
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    content=f"[timeout after {timeout}s]",
                    is_error=True,
                    error_type="timeout",
                )
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 步骤 3：按 UTF-8 解码，无法解码的字节替换为占位字符。
        # Windows 本地命令可能输出其他编码，所以能执行不等于中文输出必然无乱码。
        output = stdout_bytes.decode("utf-8", errors="replace")
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        # 先按字节判断超限，后按字符切片；当前并非严格 64 KiB 返回值上限。
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        # 步骤 4：非零退出码成为工具错误；成功但无输出时提供 [no output]。
        returncode = proc.returncode or 0
        if returncode != 0:
            return ToolResult(
                content=f"[exit {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")
