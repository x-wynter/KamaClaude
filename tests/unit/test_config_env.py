# 配置测试：在 pytest 的临时目录创建 .env/TOML，再检查实际覆盖结果。
# tmp_path 是临时 Path；monkeypatch 可切换工作目录、增删环境变量并恢复其改动。
# 这些测试仍调用完整 get_config；未替换的其他环境变量和默认 TOML 仍可能参与读取。

from __future__ import annotations

from pathlib import Path

import pytest

from kama_claude.core.config import get_config


# 以 UTF-8 写入指定文件，供各个用例准备 .env 内容。
def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# .env 中的值被加载到 config
def test_dotenv_base_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Path 的 / 拼接临时文件路径；不会仅因拼接就创建文件。
    env_file = tmp_path / ".env"
    _write_env(env_file, "KAMA_PORT=9999\n")
    # 切换当前工作目录，使 get_config 中相对路径 .env 指向本用例的文件。
    monkeypatch.chdir(tmp_path)
    # 删除已有变量以测试 .env 的作用；raising=False 允许变量原本不存在。
    monkeypatch.delenv("KAMA_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 9999


# 系统环境变量优先级高于 .env 中的值
def test_system_env_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "KAMA_PORT=9999\n")
    monkeypatch.chdir(tmp_path)
    # 模拟系统已有变量，验证 load_dotenv(override=False) 不覆盖它。
    monkeypatch.setenv("KAMA_PORT", "8888")

    cfg = get_config()

    assert cfg.port == 8888


# .env 不存在时静默跳过，使用内建默认值
def test_missing_env_file_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KAMA_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 7437


# .env 中设置的 KAMA_CONFIG 能影响 TOML 文件加载路径
def test_dotenv_before_toml_kama_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "custom.toml"
    # b 前缀表示字节串；\n 是换行，写成真正的 TOML 小节和字段。
    toml_path.write_bytes(b'[core]\nport = 5555\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, f"KAMA_CONFIG={toml_path}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KAMA_CONFIG", raising=False)
    monkeypatch.delenv("KAMA_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 5555


# 同一变量同时存在于默认值/TOML/.env/系统环境变量时，验证最终值为最高优先级来源
def test_priority_chain_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认值：7437
    # TOML：6000
    # .env：7000
    # 系统环境变量：8000（最高）
    toml_path = tmp_path / "kama.toml"
    toml_path.write_bytes(b'[core]\nport = 6000\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, "KAMA_PORT=7000\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAMA_CONFIG", str(toml_path))
    monkeypatch.setenv("KAMA_PORT", "8000")

    cfg = get_config()

    # 通过最终配置验证系统环境变量胜出；此用例并不单独覆盖所有两两优先关系。
    assert cfg.port == 8000
