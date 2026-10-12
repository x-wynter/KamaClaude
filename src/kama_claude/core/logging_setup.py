# 日志初始化：把各模块的日志交给根日志器，输出到终端和可选的滚动文件。
# Formatter 决定文字格式，Handler 决定输出位置，level 决定筛选级别。

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from kama_claude.core.config import KamaConfig

# %(levelname)s 等是 logging 的格式占位符，记录日志时由 Formatter 填入。
_TEXT_FMT = 'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"'
# 这里是 JSON 外形的字符串模板，并非 json.dumps；消息中的引号不会自动 JSON 转义。
_JSON_FMT = '{"level":"%(levelname)s","ts":"%(asctime)s","source":"%(name)s","msg":"%(message)s"}'


# 根据配置初始化 root logger：设置级别、格式，并挂载 stderr 和可选的滚动文件 handler
def setup_logging(config: KamaConfig) -> None:
    # getattr 按名称获取属性：如 getattr(logging, "INFO") 得到对应级别常量。
    # upper 统一大小写；属性不存在时回退到 logging.INFO。
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    # 条件表达式 A if 条件 else B：配置为 json 时选前者，否则选文本模板。
    fmt = _JSON_FMT if config.logging.format == "json" else _TEXT_FMT
    # datefmt 控制时间格式：年-月-日T时:分:秒；此处未显式改为 UTC 时间。
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")

    # 不传名称时取得根日志器，各模块日志默认可向根日志器传播。
    root = logging.getLogger()
    root.setLevel(level)
    # 清空原有输出处理器，避免重复挂载；clear 本身不会主动关闭旧处理器。
    root.handlers.clear()

    # StreamHandler 把日志写入输出流，这里选择标准错误流。
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    # 空字符串的布尔值为 False，因此 KAMA_LOG_FILE="" 可以禁用文件日志。
    if config.logging.file:
        log_path = Path(config.logging.file).expanduser()
        # parent 取父目录；parents=True 补齐中间目录，exist_ok=True 允许目录已存在。
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # RotatingFileHandler 在文件达到大小阈值时轮换，避免单个日志文件无限增长。
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            # 10 × 1024 × 1024 字节，即 10 MiB；backupCount=5 最多保留五份轮换备份。
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
