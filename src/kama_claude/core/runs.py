# 运行记录的路径约定：根据 run_id 计算目录和 events.jsonl 的位置。
# 这里仅构造路径，不创建目录、不读写文件；当前 ping 流程未调用这些函数。

from __future__ import annotations

from pathlib import Path

# 相对路径 runs 以进程当前工作目录为基准，不以此源码所在目录为基准。
RUNS_DIR = Path("runs")


# 返回指定 run_id 对应的目录路径
def run_dir(run_id: str) -> Path:
    # Path 重载了 / 运算符，在此用于拼接路径，不是数值除法。
    return RUNS_DIR / run_id


# 返回指定 run_id 的事件日志文件路径
def events_file(run_id: str) -> Path:
    # JSONL 通常每行一条 JSON；这里仅给出文件名，并未实现事件持久化。
    return run_dir(run_id) / "events.jsonl"
