# ═══ 文件职责：任务 ID 与运行记录路径 ═══
# 协作关系：Runner 用这些约定保存记录；Core 的历史回放用同一约定寻找事件文件。
# 阅读路线：RUNS_DIR 是相对工作目录的路径；路径计算和创建目录是不同操作。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

# 相对路径 runs 以进程当前工作目录为基准，不以此源码所在目录为基准。
RUNS_DIR = Path("runs")


# ── 函数：run_dir ──
# 用途：返回指定 run_id 对应的目录路径
def run_dir(run_id: str) -> Path:
    # Path 重载了 / 运算符，在此用于拼接路径，不是数值除法。
    return RUNS_DIR / run_id


# ── 函数：events_file ──
# 用途：返回指定 run_id 的事件日志文件路径
def events_file(run_id: str) -> Path:
    # JSONL 通常每行一条 JSON；这里仅给出文件名，并未实现事件持久化。
    return run_dir(run_id) / "events.jsonl"


# ── 函数：new_run_id ──
# 用途：生成格式为 YYYYMMDD-HHMMSS-xxxxxx 的唯一 run ID
def new_run_id() -> str:
    # 库函数：datetime.now(UTC) 取 UTC 时间；strftime 按模板生成适合文件夹名的字符串。
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    # 随机 UUID 转十六进制后取前六位，降低同秒命名冲突概率，但不是绝对唯一保证。
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-{suffix}"


# ── 函数：ensure_run_dir ──
# 用途：创建 run 目录（含父级）并返回路径
def ensure_run_dir(run_id: str) -> Path:
    path = run_dir(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
