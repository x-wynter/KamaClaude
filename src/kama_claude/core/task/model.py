# ═══ 文件职责：S3 的业务待办数据定义 ═══
# 协作关系：task_* 工具 → TaskManager → Task ↔ JSON 文件。
# 阅读路线：先看字段与 TaskStatus，再看 to_dict/from_dict；然后读 manager.py。
# 当前实现是数据对象；它与 asyncio.Task（异步执行对象）没有继承或调度关系。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Literal 来自标准库 typing：约束类型检查器认可的字符串；不会自动做运行时校验。
TaskStatus = Literal["pending", "in_progress", "completed"]


# ── 类：Task ──
# 职责：保存一条待办事项；不执行模型、不启动协程，也不自动调度依赖。
# 标准库 dataclass 自动生成构造、比较和显示方法；它不同于 Pydantic 校验模型。
@dataclass
class Task:
    # id 由 TaskManager 分配，只在当前 run 的 .tasks 目录内唯一。
    id: int
    # subject 是短标题；description 是详细要求，由 task_create 的模型参数提供。
    subject: str
    description: str
    # status 由 create 初始化、update 修改；blocked_by 存前置事项的整数 ID。
    status: TaskStatus
    blocked_by: list[int]
    # UTC 时间字符串；created_at 不变，updated_at 随状态或依赖修改而刷新。
    created_at: str
    updated_at: str

    # 序列化为 dict，字段名与 JSON 文件格式一致
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # 从 dict 构造 Task
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        # cls 是接收此类方法的类；用它构造实例，因此调用时不需要先有 Task 对象。
        # 内置 int/str 转换值，dict.get 为缺失的可选字段提供默认值。
        # 这里不会核验 status 枚举或依赖是否存在；不要把类型标注当作运行时检查。
        return cls(
            id=int(data["id"]),
            subject=str(data["subject"]),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),
            blocked_by=[int(x) for x in data.get("blocked_by", [])],
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
