# ═══ 文件职责：从命令行管理 Core 后台进程 ═══
# 协作关系：start 创建进程并记录 PID；status 探测端口；stop 根据 PID 停止进程。
# 阅读路线：先看三个 cmd_core_*；再看 TCP 检查、PID 文件和 Windows 存活检查。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from kama_claude.core.config import KamaConfig

# PID 文件保存进程编号，不是网络端口；由 core start 写入、core stop 使用。
_PID_FILE = Path.home() / ".kama" / "kama-core.pid"


# ── 函数：_ping_check ──
# 用途：尝试连接 daemon，成功则正常返回，失败则抛出 ConnectionRefusedError/OSError
async def _ping_check(config: KamaConfig) -> None:
    # 这里只探测能否建立 TCP 连接，并没有发送 core.ping 验证服务身份。
    _r, w = await asyncio.open_connection(config.host, config.port)
    w.close()
    await w.wait_closed()


# ── 函数：_windows_pid_exists ──
# 用途：Windows 的 os.kill(pid, 0) 会终止进程，不能用作“是否存活”的探测。
# 只申请查询句柄，再以零等待检查进程是否结束；不向目标发送信号。
def _windows_pid_exists(pid: int) -> bool:
    # 步骤 1：加载 Windows 系统库并声明参数与返回类型。
    # 库函数：ctypes.WinDLL 加载 Windows DLL；use_last_error 保存系统调用的错误码。
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明 HANDLE 返回类型，避免 64 位 Windows 把句柄截成 32 位整数。
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    # 步骤 2：申请进程同步句柄并处理打开失败。
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE 权限
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER：PID 已不存在
            return False
        # 权限不足也不能认定进程消失；保留 PID 文件并把错误交给调用者。
        raise ctypes.WinError(error)

    # 步骤 3：零等待检查进程是否结束，最终关闭句柄。
    try:
        # 库函数：WaitForSingleObject(handle, 0) 立即检查进程状态，零超时表示不等待。
        result: int = kernel32.WaitForSingleObject(handle, 0)
        if result == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258  # WAIT_TIMEOUT：进程尚未结束
    finally:
        kernel32.CloseHandle(handle)


# ── 函数：_running_pid ──
# 用途：读取 PID 文件并确认进程存活，进程已消失则删除文件并返回 None
def _running_pid() -> int | None:
    # 步骤 1：检查是否有 PID 文件。
    if not _PID_FILE.exists():
        return None

    # 步骤 2：读取正整数 PID 并按平台检查进程存活。
    try:
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        if pid <= 0:
            raise ValueError("PID must be positive")
        if sys.platform == "win32":
            if not _windows_pid_exists(pid):
                raise ProcessLookupError(pid)
        else:
            os.kill(pid, 0)
        return pid

    # 步骤 3：清理无效或已过期的 PID 文件。
    except (ValueError, ProcessLookupError):
        # 库函数：Path.unlink 删除 PID 文件；missing_ok=True 允许文件已被删除。
        _PID_FILE.unlink(missing_ok=True)
        return None


# ── 函数：cmd_core_status ──
# 用途：打印 daemon 当前状态（running / not running）
def cmd_core_status(config: KamaConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"running  ({config.host}:{config.port})")
    except (ConnectionRefusedError, OSError):
        print("not running")


# ── 函数：cmd_core_start ──
# 用途：在后台启动 daemon，若已在运行则提示并退出
def cmd_core_start(config: KamaConfig) -> None:
    # 步骤 1：已有监听服务时直接提示并返回。
    try:
        asyncio.run(_ping_check(config))
        print(f"already running  ({config.host}:{config.port})")
        return
    except (ConnectionRefusedError, OSError):
        pass

    # 步骤 2：使用当前 Python 解释器启动后台 Core。
    # 使用当前解释器和模块入口，避免 PATH 中另一个 Python 环境接管 daemon。
    # Popen 返回时只表示进程已创建；S2 不在这里等待端口就绪。
    proc = subprocess.Popen(
        [sys.executable, "-m", "kama_claude.core"],
        # Windows 创建无窗口后台进程；POSIX 用新会话脱离当前终端。
        start_new_session=sys.platform != "win32",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        # 库函数：DEVNULL 丢弃输出并为输入提供空流，后台进程不依赖当前终端的输入输出。
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 步骤 3：保存子进程编号，供后续停止操作使用。
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"started  pid={proc.pid}  ({config.host}:{config.port})")


# ── 函数：cmd_core_stop ──
# 用途：停止 daemon：POSIX 发送 SIGTERM；Windows 的 os.kill 使用强制终止，
# 因此 Windows 后台 stop 不保证执行 Python finally；前台 Ctrl+C 可正常清理。
def cmd_core_stop(config: KamaConfig) -> None:
    # 步骤 1：查询 PID；没有有效记录则返回。
    pid = _running_pid()
    if pid is None:
        print("not running")
        return

    # 步骤 2：请求终止进程并删除 PID 文件。
    os.kill(pid, signal.SIGTERM)
    # 库函数：Path.unlink 删除 PID 文件；missing_ok=True 允许文件已被删除。
    _PID_FILE.unlink(missing_ok=True)
    print(f"stopped  pid={pid}")
