# ═══ 文件职责：会话元数据结构 ═══
# 协作：Manager 创建/更新，Store 序列化；不是模型上下文，也不执行任务。
# 阅读顺序：两个 Literal 别名 → Session 字段 → 字典转换 → manager。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Literal 是 typing 的静态约束；dataclass 自身不会像 Pydantic 那样验证输入。
SessionStatus = Literal["active", "waiting_for_input", "closed"]
SessionMode = Literal["one_shot", "chat"]


@dataclass
class Session:
    # id 关联目录和 RPC；mode 决定一轮后关闭还是继续等输入。
    id: str
    mode: SessionMode
    status: SessionStatus  # Manager 更新；续聊执行中未重新设置为 active。
    title: str
    created_at: str
    updated_at: str
    # default_factory 每个实例创建新列表；Manager 每轮执行前追加 run_id。
    run_ids: list[str] = field(default_factory=list)

    # 将 Session 转为可写入 meta.json 的普通 dict
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_ids": list(self.run_ids),
        }

    # 从 meta.json 的 dict 还原 Session 对象
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=str(data["id"]),
            mode=data["mode"],
            status=data["status"],
            title=str(data.get("title", "")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            run_ids=[str(x) for x in data.get("run_ids", [])],
        )
