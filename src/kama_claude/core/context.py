# ═══ 文件职责：单次任务的内存状态 ═══
# 协作关系：Loop 更新此对象；Provider 读取消息历史；Runner 读取最终状态。
# 阅读路线：先看字段，再看消息追加方法，最后看状态查询和修改方法。
# 注释层次：文件概览 → 类/字段 → 函数用途 → 步骤 → 调用细节。

# ── 依赖导入 ──
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 类：ExecutionContext ──
# 职责：保存单次任务的数据；不负责网络、模型调用或文件操作。
# 一份实例对应一次任务；字段是运行状态，不是全局变量。
@dataclass
class ExecutionContext:
    # run_id 关联本任务全部事件，goal 保存最初目标，max_steps 限制模型调用轮数。
    run_id: str
    goal: str
    max_steps: int
    # default_factory 每次创建新列表，防止不同任务共享同一份消息历史。
    messages: list[dict[str, Any]] = field(default_factory=list)
    # 字段：已进入的模型请求轮数，由 Loop 递增。
    step: int = 0
    # 字段：当前任务状态：running、success 或 failed。
    status: str = "running"  # "running" | "success" | "failed"
    # 字段：失败原因，由 mark_failed 设置；初始为空。
    reason: str | None = None

    # ── 函数：__post_init__ ──
    # 用途：初始化时将 goal 作为第一条用户消息写入消息历史
    # dataclass 自动生成的 __init__ 完成字段赋值后会调用此方法。
    def __post_init__(self) -> None:
        # 空列表布尔值为 False；调用方已提供非空历史时，不再重复插入目标。
        if not self.messages:
            self.messages.append({"role": "user", "content": self.goal})

    # ── 函数：add_assistant_message ──
    # 用途：将 LLM 响应的 content blocks 追加为 assistant 消息
    def add_assistant_message(self, content: list[Any]) -> None:
        # append 把整个字典当作一条消息追加，不是把 content 中的块拆成多条消息。
        self.messages.append({"role": "assistant", "content": content})

    # ── 函数：add_tool_result ──
    # 用途：将工具调用结果追加为 user 消息；同一步的多个结果共享同一条消息
    def add_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None:
        # 步骤 1：构造工具结果块，关联原 tool_use_id。
        # tool_use_id 对应模型先前给出的调用 id，不是工具名。
        # 同一工具可以调用多次，所以仅靠 name 无法区分结果归属。
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

        # 步骤 2：失败时补上错误标记。
        # 成功结果省略 is_error 字段；失败时显式写 True，下一轮模型能看到错误。
        if is_error:
            block["is_error"] = True

        # 步骤 3：检查最后一条消息能否合并工具结果。
        # -1 取最后一个元素；条件表达式避免空列表索引越界。
        last = self.messages[-1] if self.messages else None

        # 步骤 4：可合并则追加内容，否则新增 user 消息。
        # and 从左到右短路；先检查存在和类型，再检查每个块，避免错误访问。
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]
            # all 对生成器逐项判断；显式要求非空，避免 all([]) 为 True 的情况。
            and all(b.get("type") == "tool_result" for b in last["content"])
        ):
            # last 引用原字典，修改它也就修改了 messages 中的那条消息。
            last["content"].append(block)
        else:
            # 工具反馈按供应商消息协议放入 user 角色；不代表这里有真人发送消息。
            self.messages.append({"role": "user", "content": [block]})

    # ── 函数：is_done ──
    # 用途：返回 True 表示 loop 应停止（状态不再是 running）
    def is_done(self) -> bool:
        # != 的结果是 bool；这里只读取状态，不递增步数也不判断上限。
        return self.status != "running"

    # ── 函数：mark_success ──
    # 用途：将 run 标记为成功
    def mark_success(self) -> None:
        self.status = "success"

    # ── 函数：mark_failed ──
    # 用途：将 run 标记为失败并记录原因
    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.reason = reason
