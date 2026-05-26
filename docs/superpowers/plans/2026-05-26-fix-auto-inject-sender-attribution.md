# Fix Auto Inject Sender Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 send_to 自动注入跨流上下文时的用户归属，确保群聊里的其他成员消息不会被当作触发用户消息。

**Architecture:** 只改自动注入触发用户解析和注入文本生成的最小边界。先用纯函数回归测试锁定身份标签行为，再调整群聊 `person_id` 选择顺序：显式触发消息用户优先，`ChatStreams.person_id` 只作为回退。

**Tech Stack:** Python 3.11+, pytest, Neo-MoFox plugin event handler code, `mpdt market publish .`.

---

## File Structure

- Modify: `auto_inject.py`
  - `_format_actor_label` 保持按实际消息发送者生成标签。
  - 新增一个小型私有函数 `_resolve_effective_person_id`，统一决定本轮要查询和标注的目标用户。
  - `SendToAutoContextInjectHandler.execute` 调用该函数，避免群聊优先误用 `ChatStreams.person_id`。
- Create: `test_auto_inject.py`
  - 使用 `types.SimpleNamespace` 构造消息对象。
  - 测试群聊触发用户解析优先级。
  - 测试不同 `person_id` 的群消息被标为 `其他群成员`，不是 `目标用户`。

---

### Task 1: Add Failing Tests for Sender Attribution

**Files:**
- Create: `test_auto_inject.py`
- Read/Reference: `auto_inject.py:179-222`, `auto_inject.py:652-685`

- [ ] **Step 1: Create the failing test file**

Create `test_auto_inject.py` with this content:

```python
"""Regression tests for send_to auto injection sender attribution."""

from types import SimpleNamespace

from auto_inject import (
    _format_actor_label,
    _resolve_effective_person_id,
)


def test_group_trigger_person_id_prefers_prompt_message_over_stream_person_id():
    """群聊注入应优先使用触发消息用户，而不是 ChatStreams.person_id。"""
    current_stream = SimpleNamespace(person_id="person_stream_owner", bot_id="bot_1")
    values = {
        "message": SimpleNamespace(
            person_id="person_123",
            sender_id="user_123",
            extra={},
        )
    }

    result = _resolve_effective_person_id(
        values,
        current_stream=current_stream,
        chat_type="group",
        recent_messages=[],
    )

    assert result == "person_123"


def test_actor_label_marks_non_target_group_members_as_other():
    """群聊上下文里非目标用户的发言必须明确标为其他群成员。"""
    target_label = _format_actor_label(
        sender_name="一二三",
        sender_id="123",
        sender_person_id="person_123",
        target_person_id="person_123",
        is_bot=False,
    )
    other_label = _format_actor_label(
        sender_name="阿A",
        sender_id="a",
        sender_person_id="person_a",
        target_person_id="person_123",
        is_bot=False,
    )

    assert target_label.startswith("目标用户(")
    assert "person_id=person_123" in target_label
    assert other_label.startswith("其他群成员(")
    assert "person_id=person_a" in other_label
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest test_auto_inject.py -v
```

Expected:

```text
FAILED test_auto_inject.py::test_group_trigger_person_id_prefers_prompt_message_over_stream_person_id
ImportError: cannot import name '_resolve_effective_person_id'
```

If import fails earlier because the host project modules are unavailable, run the same command from the plugin directory and inspect the missing module. Do not change production behavior to satisfy imports; only add minimal import guards in the test if necessary.

---

### Task 2: Implement Effective Trigger Person Resolution

**Files:**
- Modify: `auto_inject.py:179-201`
- Modify: `auto_inject.py:652-685`
- Test: `test_auto_inject.py`

- [ ] **Step 1: Add the resolver function**

In `auto_inject.py`, immediately after `_resolve_trigger_person_id_from_messages`, add:

```python
def _resolve_effective_person_id(
    values: dict[str, Any],
    *,
    current_stream: Any,
    chat_type: str,
    recent_messages: list[Any],
    trigger_sender_id: str = "",
) -> str:
    """解析本轮跨流注入的目标用户，群聊优先使用触发消息用户。"""
    stream_person_id = _normalize_text(getattr(current_stream, "person_id", ""))
    trigger_person_id = _extract_trigger_person_id(values)
    if chat_type == "group":
        if trigger_person_id:
            return trigger_person_id
        resolved_person_id = _resolve_trigger_person_id_from_messages(
            recent_messages,
            bot_id=str(getattr(current_stream, "bot_id", "") or ""),
            trigger_sender_id=trigger_sender_id,
        )
        return resolved_person_id or stream_person_id
    return trigger_person_id or stream_person_id
```

- [ ] **Step 2: Update execute to use the resolver**

Replace the block in `auto_inject.py` that starts at the comment `# 解析当前流的平台和 person_id` and ends before `if not platform or not person_id:` with:

```python
        # 解析当前流的平台和 person_id。
        # 群聊必须优先使用本轮触发消息用户；ChatStreams.person_id 在群聊里可能只是流级占位，
        # 不能用它把所有注入消息都归因到同一个用户。
        current_rows = await (
            QueryBuilder(ChatStreams)
            .filter(stream_id=stream_id)
            .limit(1)
            .all()
        )
        if not current_rows:
            return EventDecision.SUCCESS, params

        current_stream = current_rows[0]
        platform = str(getattr(current_stream, "platform", "") or "")
        chat_type = str(getattr(current_stream, "chat_type", "") or "")
        trigger_sender_id = _extract_trigger_sender_id(values)
        recent_msgs: list[Any] = []
        if chat_type == "group":
            recent_msgs = await (
                QueryBuilder(Messages)
                .filter(stream_id=stream_id, platform=platform)
                .order_by("-time")
                .limit(10)
                .all()
            )
        person_id = _resolve_effective_person_id(
            values,
            current_stream=current_stream,
            chat_type=chat_type,
            recent_messages=recent_msgs,
            trigger_sender_id=trigger_sender_id,
        )
```

- [ ] **Step 3: Run tests to verify GREEN**

Run:

```bash
pytest test_auto_inject.py -v
```

Expected:

```text
2 passed
```

---

### Task 3: Verify Existing Behavior and Version Stability

**Files:**
- Check: `manifest.json:3`
- Check: `auto_inject.py`
- Test: `test_auto_inject.py`

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest test_auto_inject.py -v
```

Expected:

```text
2 passed
```

- [ ] **Step 2: Run syntax check for plugin Python files**

Run:

```bash
python -m py_compile auto_inject.py context_lookup.py action.py relay.py tools.py plugin.py
```

Expected: command exits with status 0 and no output.

- [ ] **Step 3: Confirm version unchanged**

Run:

```bash
grep -n '"version"' manifest.json
```

Expected:

```text
3:  "version": "3.0.0",
```

---

### Task 4: Publish Without Version Bump

**Files:**
- Check: `manifest.json`
- Command: `mpdt market publish .`

- [ ] **Step 1: Check working tree before publish**

Run:

```bash
git status --short
```

Expected: shows only intended changes, such as:

```text
 M auto_inject.py
?? test_auto_inject.py
?? __pycache__/
```

Do not include `__pycache__/` in any commit or package-specific source change.

- [ ] **Step 2: Publish to market**

Run:

```bash
mpdt market publish .
```

Expected: command exits successfully and reports the plugin was published. If it asks for interactive confirmation, answer according to the prompt without changing `manifest.json` version.

- [ ] **Step 3: Report result**

Report:

```text
已修复自动注入用户归属，manifest 版本保持 3.0.0，并已执行 mpdt market publish .
```

Include any publish URL or package identifier printed by the command, only if the command provides one.

---

## Self-Review

- Spec coverage: covers group trigger user priority, per-message sender labels, no version bump, publish command.
- Placeholder scan: no TBD/TODO/implement later placeholders remain.
- Type consistency: `_resolve_effective_person_id` accepts `dict[str, Any]`, `Any` stream, `list[Any]` recent messages, and returns `str`; call site matches that signature.
