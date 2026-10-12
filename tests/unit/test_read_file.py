# 读文件测试：在临时目录构造输入，覆盖正常内容、缺失、截断和路径检查。

from __future__ import annotations

from pathlib import Path

import pytest

from kama_claude.core.tools.builtin.read_file import ReadFileTool


# 功能：读文件工具返回已知文本。
# 设计：使用 tmp_path 文件隔离真实项目数据，逐字比较内容。
async def test_read_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    result = await ReadFileTool().invoke({"path": str(f)})
    assert not result.is_error
    assert result.content == "hello world"


# 功能：不存在文件由工具直接抛 FileNotFoundError。
# 设计：绕过 invoke_tool，检查工具自身而非外层错误包装。
async def test_file_not_found_raises(tmp_path: Path) -> None:
    # 上下文块必须抛出指定异常测试才通过；不抛异常或抛其他异常都会失败。
    with pytest.raises(FileNotFoundError):
        await ReadFileTool().invoke({"path": str(tmp_path / "missing.txt")})


# 功能：包含上级目录部分的路径被拒绝。
# 设计：使用最短 ../ 路径，在文件读取前触发检查。
async def test_path_traversal_dotdot_raises() -> None:
    # 上下文块必须抛出指定异常测试才通过；不抛异常或抛其他异常都会失败。
    with pytest.raises(PermissionError):
        await ReadFileTool().invoke({"path": "../secret.txt"})


# 功能：嵌套路径中的 .. 同样被拒绝。
# 设计：把 .. 放在路径中部，避免实现只判断开头。
async def test_path_traversal_nested_raises() -> None:
    # 上下文块必须抛出指定异常测试才通过；不抛异常或抛其他异常都会失败。
    with pytest.raises(PermissionError):
        await ReadFileTool().invoke({"path": "subdir/../../etc/passwd"})


# 功能：超过 512 KB 的输出带有截断标记。
# 设计：单字节字符使字节数等于字符数，便于精确比较截断前缀。
async def test_truncation_over_512kb(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_bytes(b"x" * (600 * 1024))
    result = await ReadFileTool().invoke({"path": str(f)})
    assert not result.is_error
    assert result.content.endswith("[truncated]")
    # Actual text content is exactly 512KB worth of 'x' chars
    assert result.content.startswith("x" * (512 * 1024))


# 功能：恰好达到上限时不截断。
# 设计：边界长度验证实现使用 > 而非 >=。
async def test_exact_512kb_is_not_truncated(tmp_path: Path) -> None:
    f = tmp_path / "exact.txt"
    f.write_bytes(b"y" * (512 * 1024))
    result = await ReadFileTool().invoke({"path": str(f)})
    assert not result.is_error
    assert not result.content.endswith("[truncated]")
    assert len(result.content) == 512 * 1024


# 功能：空文件返回成功且正文为空。
# 设计：实际创建零字节文件，区分空文件与文件不存在。
async def test_empty_file_returns_empty_content(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = await ReadFileTool().invoke({"path": str(f)})
    assert not result.is_error
    assert result.content == ""
