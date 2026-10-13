# ═══ 文件职责：日志输出设置 ═══
# 协作关系：调用方传入 KamaConfig；本文件配置根日志器，其他模块按模块名记录日志。
# 阅读路线：按格式选择 → 根日志器 → 终端输出 → 文件输出阅读。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from kama_claude.core.config import KamaConfig

# %(levelname)s 等是 logging 的格式占位符，记录日志时由 Formatter 填入。
_TEXT_FMT = 'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"'
# 这是字符串模板；不像 json.dumps，它不会自动转义日志正文中的引号。
_JSON_FMT = '{"level":"%(levelname)s","ts":"%(asctime)s","source":"%(name)s","msg":"%(message)s"}'


# ── 函数：setup_logging ──
# 用途：根据配置初始化 root logger：设置级别、格式，并挂载 stderr 和可选的滚动文件 handler
def setup_logging(config: KamaConfig) -> None:
    # 步骤 1：选择日志级别和格式化器。
    # getattr 按名称获取属性：如 getattr(logging, "INFO") 得到对应级别常量。
    # upper 统一大小写；属性不存在时回退到 logging.INFO。
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    # 条件表达式 A if 条件 else B：配置为 json 时选前者，否则选文本模板。
    fmt = _JSON_FMT if config.logging.format == "json" else _TEXT_FMT
    # datefmt 控制时间格式：年-月-日T时:分:秒；此处未显式改为 UTC 时间。
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")

    # 步骤 2：配置根日志器并清空旧输出列表。
    # 不传名称时取得根日志器，各模块日志默认可向根日志器传播。
    root = logging.getLogger()
    root.setLevel(level)
    # 清空原有输出处理器，避免重复挂载；clear 本身不会主动关闭旧处理器。
    root.handlers.clear()

    # 步骤 3：添加标准错误输出。
    # StreamHandler 把日志写入输出流，这里选择标准错误流。
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    # 步骤 4：按需添加带大小轮换的文件输出。
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
