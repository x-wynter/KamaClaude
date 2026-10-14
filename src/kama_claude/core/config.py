# ═══ 文件职责：配置定义与分层加载 ═══
# 协作关系：Core、CLI、TUI 各自调用 get_config，得到自己的配置对象。
# 阅读路线：先看五个 dataclass，再读 get_config；最后展开 TOML 和环境变量覆盖规则。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
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
_DEFAULT_MAX_STEPS = 20
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_TRACE_FILE = "~/.kama/traces/daemon.jsonl"


# ── 类：LoggingConfig ──
# 职责：日志输出级别、位置和格式。
# 装饰器自动生成 __init__、__repr__、__eq__ 等方法，适合保存配置数据。
# 字段的类型提示不会自动校验，也不会自动把字符串转换为整数。
# 日志配置是独立对象，稍后放在 KamaConfig.logging 字段中。
@dataclass
class LoggingConfig:
    # 字段：日志最低输出级别。
    level: str = _DEFAULT_LOG_LEVEL
    # 字段：日志文件路径；空字符串表示不写文件。
    file: str = _DEFAULT_LOG_FILE
    # 字段：日志输出格式，当前识别 text 和 json。
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json"


# ── 类：AgentConfig ──
# 职责：Agent 执行轮数限制。
# S1 新增任务配置；max_steps 限制模型调用轮数，不是工具调用总数。
@dataclass
class AgentConfig:
    # 字段：最多调用模型的轮数，不是工具调用次数。
    max_steps: int = _DEFAULT_MAX_STEPS


# ── 类：LlmConfig ──
# 职责：模型名称和预留的路由配置。
# 模型配置独立成对象；router 当前只是保存字符串，S1 未据此实现动态路由。
@dataclass
class LlmConfig:
    # 字段：传给模型 Provider 的模型名称。
    default_model: str = _DEFAULT_MODEL
    # 字段：保存路由策略名称；当前 Provider 固定使用静态模型。
    router: str = "static"  # S4 仍未实现动态路由；其他字符串只是配置值。


# ── 类：TraceConfig ──
# 职责：控制统一时间线；enabled 开关，file 路径，include_llm_payload 控制 LLM 详细内容。
# 关闭 payload 不会清除 IPC params、事件 token 或工具输出；不是全系统内容脱敏开关。
@dataclass
class TraceConfig:
    enabled: bool = True
    file: str = _DEFAULT_TRACE_FILE
    include_llm_payload: bool = True  # false 时 LLM 记录只保留摘要


# ── 类：KamaConfig ──
# 职责：汇总地址、日志、Agent、模型和 trace；不同启动进程各自加载一份。
@dataclass
class KamaConfig:
    # 字段：Core 监听或客户端连接的主机地址。
    host: str = _DEFAULT_HOST
    # 字段：Core 监听或客户端连接的 TCP 端口。
    port: int = _DEFAULT_PORT
    # field 来自 dataclasses；default_factory 接收可调用对象而不是调用结果。
    # 每次省略 logging 参数时都会调用 LoggingConfig()，避免多个配置共享默认对象。
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # default_factory 为每份 KamaConfig 分别创建子配置，避免配置实例共享可变状态。
    agent: AgentConfig = field(default_factory=AgentConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)


# ── 函数：get_config ──
# 用途：构建并返回运行时配置：默认值 → TOML → .env → 系统环境变量（后者优先级最高）
def get_config() -> KamaConfig:
    # 步骤 1：创建带默认值的配置对象。
    # 先得到包含默认值的实例，下面的辅助函数直接修改它的属性。
    config = KamaConfig()

    # 步骤 2：将 .env 补入环境变量，并确定 TOML 路径。
    # .env 必须在读取 KAMA_CONFIG 之前加载，以便 .env 中的 KAMA_CONFIG 能影响 TOML 路径
    # 相对路径 .env 以当前工作目录为基准。override=False 保留已有同名环境变量。
    load_dotenv(".env", override=False)

    # os.environ 类似字典；get 的第二个参数是在键不存在时使用的默认值。
    # Path 把字符串变成路径对象，expanduser 将开头的 ~ 展开为用户主目录。
    config_path = Path(os.environ.get("KAMA_CONFIG", _DEFAULT_CONFIG_PATH)).expanduser()

    # 步骤 3：存在 TOML 时读取、校验并覆盖默认值。
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

    # 步骤 4：最后应用环境变量，返回最终配置。
    # 最后应用环境变量，所以它们覆盖 TOML；其中已包含从 .env 补入的变量。
    _apply_env(config)
    return config


# ── 函数：_apply_toml ──
# 用途：将已解析的 TOML 根表写入 config；未知小节或类型错误时退出进程
def _apply_toml(config: KamaConfig, data: dict[str, Any]) -> None:
    # 步骤 1：集合差集找出未知配置章节；S3 允许新增 [trace]。
    unknown = set(data.keys()) - {"core", "logging", "agent", "llm", "trace"}
    if unknown:
        # sorted 排序得到列表，join 用逗号拼接，保证错误信息顺序稳定。
        raise SystemExit(f"Unknown top-level config keys: {', '.join(sorted(unknown))}")

    # 步骤 2：处理监听地址和端口。
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

    # 步骤 3：处理日志输出配置。
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

    # 步骤 4：处理 Agent 步数限制。
    # TOML 的 [agent] 解析为嵌套字典；只覆盖明确提供的字段。
    if "agent" in data:
        agent = data["agent"]
        if not isinstance(agent, dict):
            raise SystemExit("Config error: [agent] must be a table")
        unknown_agent: set[str] = set(agent.keys()) - {"max_steps"}
        if unknown_agent:
            raise SystemExit(f"Unknown [agent] keys: {', '.join(sorted(unknown_agent))}")
        if "max_steps" in agent:
            val = agent["max_steps"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: agent.max_steps must be a positive integer")
            config.agent.max_steps = val

    # 步骤 5：处理模型配置。
    # S1 增加 [llm] 小节，仍先拒绝未知键再检查已知字段类型。
    if "llm" in data:
        llm = data["llm"]
        if not isinstance(llm, dict):
            raise SystemExit("Config error: [llm] must be a table")
        unknown_llm: set[str] = set(llm.keys()) - {"default_model", "router"}
        if unknown_llm:
            raise SystemExit(f"Unknown [llm] keys: {', '.join(sorted(unknown_llm))}")
        if "default_model" in llm:
            val = llm["default_model"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.default_model must be a string")
            config.llm.default_model = val
        if "router" in llm:
            val = llm["router"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.router must be a string")
            config.llm.router = val

    # 步骤 6：读取新增 [trace]，分别校验布尔值和文件路径字符串。
    if "trace" in data:
        trace = data["trace"]
        if not isinstance(trace, dict):
            raise SystemExit("Config error: [trace] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {"enabled", "file", "include_llm_payload"}
        if unknown_trace:
            raise SystemExit(f"Unknown [trace] keys: {', '.join(sorted(unknown_trace))}")
        if "enabled" in trace:
            val = trace["enabled"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.enabled must be a boolean")
            config.trace.enabled = val
        if "file" in trace:
            val = trace["file"]
            if not isinstance(val, str):
                raise SystemExit("Config error: trace.file must be a string")
            config.trace.file = val
        if "include_llm_payload" in trace:
            val = trace["include_llm_payload"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.include_llm_payload must be a boolean")
            config.trace.include_llm_payload = val


# ── 函数：_apply_env ──
# 用途：用 KAMA_* 环境变量覆盖 config 中对应字段（若变量已设置）
def _apply_env(config: KamaConfig) -> None:
    # 步骤 1：覆盖地址和端口；端口需要转换为整数。
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

    # 步骤 2：覆盖日志级别、文件与格式。
    log_level = os.environ.get("KAMA_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("KAMA_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("KAMA_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format

    # 步骤 3：转换并验证最大步数。
    # 环境变量都是字符串，需先 int 转换，再检查正数；不能直接与整数比较。
    max_steps_str = os.environ.get("KAMA_MAX_STEPS")
    if max_steps_str is not None:
        try:
            val = int(max_steps_str)
            if val <= 0:
                raise SystemExit(
                    "Config error: KAMA_MAX_STEPS must be a positive integer,"
                    f" got: {max_steps_str!r}"
                )
            config.agent.max_steps = val
        except ValueError:
            raise SystemExit(
                f"Config error: KAMA_MAX_STEPS must be an integer, got: {max_steps_str!r}"
            )

    # 步骤 4：覆盖默认模型名称。
    default_model = os.environ.get("KAMA_LLM_DEFAULT_MODEL")
    if default_model is not None:
        config.llm.default_model = default_model

    # 步骤 5：环境变量中的 0/false/no（忽略大小写）为假，其他字符串均为真。
    trace_enabled = os.environ.get("KAMA_TRACE_ENABLED")
    if trace_enabled is not None:
        config.trace.enabled = trace_enabled.lower() not in ("0", "false", "no")

    trace_file = os.environ.get("KAMA_TRACE_FILE")
    if trace_file is not None:
        config.trace.file = trace_file

    trace_payload = os.environ.get("KAMA_TRACE_INCLUDE_LLM_PAYLOAD")
    if trace_payload is not None:
        config.trace.include_llm_payload = trace_payload.lower() not in ("0", "false", "no")
