# ═══ 文件职责：任务清单的文件存储与依赖清理 ═══
# 协作关系：Runner 创建一份 Manager → 四个 task_* 工具共用 → .tasks/task_N.json。
# 阅读路线：先读 create/get/update，再读 _load/_save，最后 list_all/_clear_dependency。
# 所有方法都是同步函数；工具外层 async 不会自动把这些文件操作移到线程。

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kama_claude.core.task.model import Task, TaskStatus


# 返回标准库 datetime 生成的带 UTC 时区 ISO 时间，用于磁盘记录。
def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── 类：TaskManager ──
# 职责：在一个 run 的目录内同步增查改任务；四个任务工具共享它。
# 字段：_dir 保存 .tasks 路径；_next_id 在扫描旧文件后确定，成功创建后递增。
# 没有后台调度器、锁、事务或图算法；依赖是保存和清理的列表。
class TaskManager:
    # 初始化：确保目录存在，扫描现有文件确定下一个 ID
    def __init__(self, tasks_dir: Path) -> None:
        # 步骤 1：标准库 Path.mkdir 补齐目录；对象初始化时立即访问磁盘。
        self._dir = tasks_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        # 步骤 2：扫描编号避免重启后直接从 1 覆盖旧文件；不是多进程原子分配。
        self._next_id = self._max_id() + 1

    # 扫描目录中 task_*.json 文件，返回最大 ID（无文件则返回 0）
    def _max_id(self) -> int:
        # Path.glob 只枚举匹配文件；stem 去扩展名；split 取编号；isdigit 过滤非数字。
        ids = [
            int(f.stem.split("_")[1])
            for f in self._dir.glob("task_*.json")
            if f.stem.split("_")[1].isdigit()
        ]
        # 内置 max 求最大编号；空列表分支返回 0，下一次创建从 1 开始。
        return max(ids) if ids else 0

    # 读取指定 ID 的任务文件
    def _load(self, task_id: int) -> Task:
        path = self._dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"task {task_id} not found")
        # 标准库 json.loads：JSON 字符串 → dict；项目 from_dict：dict → Task。
        return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # 将任务写入对应 JSON 文件
    def _save(self, task: Task) -> None:
        path = self._dir / f"task_{task.id}.json"
        # ensure_ascii=False 保留中文；显式 UTF-8 保证 Windows 与其他平台文件可互读。
        # write_text 覆盖整个文件；当前实现不使用临时文件 + 原子替换。
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # 创建新任务，写入 JSON 文件，返回 Task
    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
    ) -> Task:
        # 步骤 1：创建时检查前置 ID 对应文件存在；不检查其状态或依赖环。
        for dep_id in (blocked_by or []):
            if not (self._dir / f"task_{dep_id}.json").exists():
                raise ValueError(f"blocked_by task {dep_id} not found")
        # 步骤 2：组装 pending 记录；list 复制依赖，避免直接保存调用方的列表引用。
        now = _now()
        task = Task(
            id=self._next_id,
            subject=subject,
            description=description,
            status="pending",
            blocked_by=list(blocked_by or []),
            created_at=now,
            updated_at=now,
        )
        # 步骤 3：先写入成功再增加编号，将新 Task 交回工具层序列化。
        self._save(task)
        self._next_id += 1
        return task

    # 读取指定 ID 的任务
    def get(self, task_id: int) -> Task:
        return self._load(task_id)

    # 更新任务状态或依赖列表；status="completed" 时自动清理其他任务的 blocked_by
    def update(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        add_blocked_by: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
    ) -> Task:
        # 步骤 1：读出现状；有 status 参数时验证字符串并更新状态。
        task = self._load(task_id)
        if status is not None:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status!r}")
            task.status = status
            if status == "completed":
                # 完成会修改其他任务文件；当前不阻止带 blocked_by 的任务被标成 completed。
                self._clear_dependency(task_id)
        # 步骤 2：集合去重追加依赖，再移除指定依赖；新增 ID 在 update 中不检查存在性。
        # set 不承诺保留顺序；不要依赖 blocked_by 的展示顺序。
        if add_blocked_by:
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))
        if remove_blocked_by:
            task.blocked_by = [x for x in task.blocked_by if x not in remove_blocked_by]
        # 步骤 3：刷新时间并覆盖保存，返回更新后的 Task。
        task.updated_at = _now()
        self._save(task)
        return task

    # 返回所有任务，按 ID 升序排列
    def list_all(self) -> list[Task]:
        # 步骤 1：按数值 ID 排序（避免 task_10 排在 task_2 前），逐个恢复对象。
        # sorted 的 key 接受函数，lambda p 返回排序依据。
        tasks = []
        for f in sorted(self._dir.glob("task_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            try:
                tasks.append(Task.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            # 格式错误记录被略过；文件名排序和操作系统 I/O 错误不在此捕获范围。
            except (ValueError, KeyError):
                pass
        return tasks

    # 将 completed_id 从所有其他任务的 blocked_by 列表中移除
    def _clear_dependency(self, completed_id: int) -> None:
        # 遍历所有任务文件，去掉已完成 ID；只更新真正受影响的记录。
        for f in self._dir.glob("task_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, json.JSONDecodeError):
                continue
            blocked = [int(x) for x in data.get("blocked_by", [])]
            if completed_id in blocked:
                data["blocked_by"] = [x for x in blocked if x != completed_id]
                data["updated_at"] = _now()
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 格式化任务列表摘要，供 task_list 工具返回给 Agent
    def format_list(self) -> str:
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        # 字典将状态映射为终端符号；返回纯文本摘要，不是 JSON 也不是任务事件。
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = []
        for t in tasks:
            blocked = f" (blocked by: {t.blocked_by})" if t.blocked_by else ""
            lines.append(f"{marker.get(t.status, '[?]')} #{t.id}: {t.subject}{blocked}")
        return "\n".join(lines)
