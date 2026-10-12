# 运行时配置：先创建默认对象，再用 TOML 和环境变量覆盖字段。
# .env 先载入进程环境以选择 TOML 路径，但其中的业务配置最后才应用。
# 优先级：默认值 < TOML < .env < 原有环境变量。

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# dotenv 是第三方库 python-dotenv；os、tomllib、dataclasses、pathlib 是标准库。
from dotenv import load_dotenv

# 大写名称约定为常量，开头下划线约定为模块内部使用；Python 不强制禁止修改。
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7437
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_FILE = "~/.kama/logs/core.log"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_CONFIG_PATH = "~/.kama/config.toml"


# 装饰器自动生成 __init__、__repr__、__eq__ 等方法，适合保存配置数据。
# 字段的类型提示不会自动校验，也不会自动把字符串转换为整数。
@dataclass
# 日志配置是独立对象，稍后放在 KamaConfig.logging 字段中。
class LoggingConfig:
    level: str = _DEFAULT_LOG_LEVEL
    file: str = _DEFAULT_LOG_FILE
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json"


@dataclass
# 例如 KamaConfig(port=9000) 只替换端口，其他字段仍取默认值。
class KamaConfig:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    # field 来自 dataclasses；default_factory 接收可调用对象而不是调用结果。
    # 每次省略 logging 参数时都会调用 LoggingConfig()，避免多个配置共享默认对象。
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# 构建并返回运行时配置：默认值 → TOML → .env → 系统环境变量（后者优先级最高）
def get_config() -> KamaConfig:
    # 先得到包含默认值的实例，下面的辅助函数直接修改它的属性。
    config = KamaConfig()

    # .env 必须在读取 KAMA_CONFIG 之前加载，以便 .env 中的 KAMA_CONFIG 能影响 TOML 路径
    # 相对路径 .env 以当前工作目录为基准。override=False 保留已有同名环境变量。
    load_dotenv(".env", override=False)

    # os.environ 类似字典；get 的第二个参数是在键不存在时使用的默认值。
    # Path 把字符串变成路径对象，expanduser 将开头的 ~ 展开为用户主目录。
    config_path = Path(os.environ.get("KAMA_CONFIG", _DEFAULT_CONFIG_PATH)).expanduser()

    # exists 只检查路径存在；若不存在则跳过 TOML，继续应用环境变量。
    if config_path.exists():
        try:
            # with 离开代码块时自动关闭文件，包括异常路径；rb 表示只读二进制模式。
            # tomllib.load 要求二进制文件对象，解析后得到包含嵌套字典的配置数据。
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        # 这里只捕获 TOML 格式错误；权限错误等其他异常不在此分支处理。
        except tomllib.TOMLDecodeError as e:
            # 字符串用作退出提示；from e 显式保留原始异常与新异常的因果关系。
            raise SystemExit(f"Config parse error ({config_path}): {e}") from e
        # 此函数原地修改 config，不需要用返回值重新赋给 config。
        _apply_toml(config, data)

    # 最后应用环境变量，所以它们覆盖 TOML；其中已包含从 .env 补入的变量。
    _apply_env(config)
    return config


# 将已解析的 TOML 根表写入 config；未知小节或类型错误时退出进程
def _apply_toml(config: KamaConfig, data: dict[str, Any]) -> None:
    # set 将键名变成集合；集合差集 A - B 得到实际出现但未允许的键。
    unknown = set(data.keys()) - {"core", "logging"}
    if unknown:
        # sorted 排序得到列表，join 用逗号拼接，保证错误信息顺序稳定。
        raise SystemExit(f"Unknown top-level config keys: {', '.join(sorted(unknown))}")

    if "core" in data:
        core = data["core"]
        # isinstance 是内置函数，检查实际类型；TOML 的 [core] 应解析为字典。
        if not isinstance(core, dict):
            raise SystemExit("Config error: [core] must be a table")
        unknown_core: set[str] = set(core.keys()) - {"host", "port"}
        if unknown_core:
            raise SystemExit(f"Unknown [core] keys: {', '.join(sorted(unknown_core))}")
        if "host" in core:
            val = core["host"]
            if not isinstance(val, str):
                raise SystemExit("Config error: core.host must be a string")
            config.host = val
        if "port" in core:
            val = core["port"]
            # 注意 Python 的 bool 是 int 的子类，此判断也会接受布尔值；它不检查端口范围。
            if not isinstance(val, int):
                raise SystemExit("Config error: core.port must be an integer")
            config.port = val

    if "logging" in data:
        log = data["logging"]
        if not isinstance(log, dict):
            raise SystemExit("Config error: [logging] must be a table")
        unknown_log: set[str] = set(log.keys()) - {"level", "file", "format"}
        if unknown_log:
            raise SystemExit(f"Unknown [logging] keys: {', '.join(sorted(unknown_log))}")
        # 遍历三个允许的日志字段；只覆盖文件里明确提供的值。
        for key in ("level", "file", "format"):
            if key in log:
                val = log[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: logging.{key} must be a string")
                # 内置 setattr(obj, name, value) 按字符串设置属性。
                # 例如 key == "level" 时，等价于 config.logging.level = val。
                setattr(config.logging, key, val)


# 用 KAMA_* 环境变量覆盖 config 中对应字段（若变量已设置）
def _apply_env(config: KamaConfig) -> None:
    # 环境变量读取结果是字符串或 None；is not None 区分未设置与设置为空字符串。
    host = os.environ.get("KAMA_HOST")
    if host is not None:
        config.host = host

    port_str = os.environ.get("KAMA_PORT")
    if port_str is not None:
        try:
            # int 显式转换十进制端口字符串；无法转换时抛出 ValueError。
            config.port = int(port_str)
        except ValueError:
            # !r 使用 repr 形式显示值，例如为字符串保留引号，便于看出空白和空字符串。
            raise SystemExit(f"Config error: KAMA_PORT must be an integer, got: {port_str!r}")

    log_level = os.environ.get("KAMA_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("KAMA_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("KAMA_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format
