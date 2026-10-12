# 上下文单元测试：直接检查消息列表与状态，不调用模型或网络。

from __future__ import annotations

from kama_claude.core.context import ExecutionContext


# 功能：目标自动成为第一条 user 消息。
# 设计：比较完整列表，连同角色、内容和条数一起检查。
def test_initial_message_is_goal() -> None:
    ctx = ExecutionContext(run_id="r1", goal="test goal", max_steps=5)
    assert ctx.messages == [{"role": "user", "content": "test goal"}]


# 功能：初始状态仍允许执行循环。
# 设计：只构造上下文，排除模型或工具的影响。
def test_is_done_returns_false_when_running() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    assert not ctx.is_done()


# 功能：成功状态能终止循环且初始 reason 保持空值。
# 设计：同时检查 is_done、status 和 reason，覆盖调用者读取的三个字段。
def test_mark_success() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.mark_success()
    assert ctx.is_done()
    assert ctx.status == "success"
    assert ctx.reason is None


# 功能：失败状态保存原因并终止循环。
# 设计：传入具体失败原因，检查方法没有丢失调用者信息。
def test_mark_failed() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.mark_failed("exceeded_max_steps")
    assert ctx.is_done()
    assert ctx.status == "failed"
    assert ctx.reason == "exceeded_max_steps"


# 功能：模型内容作为一条 assistant 消息追加。
# 设计：用最后一项与完整字典比较，确认没有拆散内容块。
def test_add_assistant_message_appended() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    content = [{"type": "text", "text": "I'll help"}]
    ctx.add_assistant_message(content)
    assert ctx.messages[-1] == {"role": "assistant", "content": content}


# 功能：工具结果使用 user 角色并关联原调用 ID。
# 设计：先写 tool_use，再写对应结果，模拟真实消息顺序。
def test_add_tool_result_creates_user_message() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.add_assistant_message(
        [{"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {"path": "x"}}]
    )
    ctx.add_tool_result("toolu_01", "file content")
    last = ctx.messages[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "toolu_01"
    assert last["content"][0]["content"] == "file content"


# 功能：同一步的两个工具结果合并到同一条消息。
# 设计：比较总消息数和块顺序，避免只验证结果存在而漏掉分组错误。
def test_multiple_tool_results_share_one_message() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.add_assistant_message([
        {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {}},
        {"type": "tool_use", "id": "toolu_02", "name": "read_file", "input": {}},
    ])
    ctx.add_tool_result("toolu_01", "result A")
    ctx.add_tool_result("toolu_02", "result B")

    # goal + assistant + tool_results（合并为一条）
    assert len(ctx.messages) == 3
    last = ctx.messages[-1]
    assert last["role"] == "user"
    assert len(last["content"]) == 2
    assert last["content"][0]["tool_use_id"] == "toolu_01"
    assert last["content"][1]["tool_use_id"] == "toolu_02"


# 功能：失败工具结果保留错误标记和内容。
# 设计：显式传 is_error=True，再检查序列化前的块字典。
def test_tool_result_error_flag() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.add_assistant_message(
        [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
    )
    ctx.add_tool_result("t1", "something failed", is_error=True)
    block = ctx.messages[-1]["content"][0]
    assert block["is_error"] is True
    assert block["content"] == "something failed"


# 功能：跨步骤消息保持用户与助手交替顺序。
# 设计：提取 role 列表断言顺序，不依赖内容文字。
def test_message_order_across_steps() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=5)
    ctx.add_assistant_message([{"type": "text", "text": "step 1 plan"}])
    ctx.add_tool_result("t1", "tool result")
    ctx.add_assistant_message([{"type": "text", "text": "step 2 plan"}])

    # 列表推导式从每条消息提取 role，得到可以直接比较顺序的简单列表。
    roles = [m["role"] for m in ctx.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


# 功能：新上下文的步数从零开始。
# 设计：仅检查初始值，后续递增由 Loop 测试覆盖。
def test_step_counter_default() -> None:
    ctx = ExecutionContext(run_id="r1", goal="g", max_steps=20)
    assert ctx.step == 0
