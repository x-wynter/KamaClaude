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


# 尝试连接 daemon，成功则正常返回，失败则抛出 ConnectionRefusedError/OSError
async def _ping_check(config: KamaConfig) -> None:
    # 这里只探测能否建立 TCP 连接，并没有发送 core.ping 验证服务身份。
    _r, w = await asyncio.open_connection(config.host, config.port)
    w.close()
    await w.wait_closed()


# Windows 的 os.kill(pid, 0) 会终止进程，不能用作“是否存活”的探测。
# 只申请查询句柄，再以零等待检查进程是否结束；不向目标发送信号。
def _windows_pid_exists(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明 HANDLE 返回类型，避免 64 位 Windows 把句柄截成 32 位整数。
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE 权限
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER：PID 已不存在
            return False
        # 权限不足也不能认定进程消失；保留 PID 文件并把错误交给调用者。
        raise ctypes.WinError(error)
    try:
        result: int = kernel32.WaitForSingleObject(handle, 0)
        if result == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258  # WAIT_TIMEOUT：进程尚未结束
    finally:
        kernel32.CloseHandle(handle)


# 读取 PID 文件并确认进程存活，进程已消失则删除文件并返回 None
def _running_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
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
    except (ValueError, ProcessLookupError):
        _PID_FILE.unlink(missing_ok=True)
        return None


# 打印 daemon 当前状态（running / not running）
def cmd_core_status(config: KamaConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"running  ({config.host}:{config.port})")
    except (ConnectionRefusedError, OSError):
        print("not running")


# 在后台启动 daemon，若已在运行则提示并退出
def cmd_core_start(config: KamaConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"already running  ({config.host}:{config.port})")
        return
    except (ConnectionRefusedError, OSError):
        pass

    # 使用当前解释器和模块入口，避免 PATH 中另一个 Python 环境接管 daemon。
    # Popen 返回时只表示进程已创建；S2 不在这里等待端口就绪。
    proc = subprocess.Popen(
        [sys.executable, "-m", "kama_claude.core"],
        # Windows 创建无窗口后台进程；POSIX 用新会话脱离当前终端。
        start_new_session=sys.platform != "win32",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"started  pid={proc.pid}  ({config.host}:{config.port})")


# 停止 daemon：POSIX 发送 SIGTERM；Windows 的 os.kill 使用强制终止，
# 因此 Windows 后台 stop 不保证执行 Python finally；前台 Ctrl+C 可正常清理。
def cmd_core_stop(config: KamaConfig) -> None:
    pid = _running_pid()
    if pid is None:
        print("not running")
        return
    os.kill(pid, signal.SIGTERM)
    _PID_FILE.unlink(missing_ok=True)
    print(f"stopped  pid={pid}")
