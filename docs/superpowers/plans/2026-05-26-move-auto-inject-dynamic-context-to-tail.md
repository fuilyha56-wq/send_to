# Move Auto Inject Dynamic Context To Tail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 send_to 自动注入的动态跨流上下文明确以后置 turn 内容形式进入 prompt，减少对 NFC 固定前缀缓存的破坏。

**Architecture:** 保持现有自动注入入口不变，只在 `context_contributions` payload 中增加后置排序语义，并在注入文本标题中声明这是本轮末尾动态补充上下文。`values.extra` fallback 继续追加到末尾，不改版本号。

**Tech Stack:** Python 3.11+, pytest, Neo-MoFox plugin event handler code, mpdt market publish.

---

## File Structure

- Modify: `auto_inject.py`
  - `_build_injection_text` 的标题改为“本轮末尾动态补充上下文”，说明不是用户新消息、不是系统规则。
  - `context_contributions.append(...)` 的 dict 增加 `placement: "tail"`，并将 `priority` 调低为 `-100`。
  - `values.extra` fallback 保持 `existing_extra + separator + injection_text`，继续尾部追加。
- Modify: `test_auto_inject.py`
  - 导入 `_build_injection_text`。
  - 增加测试：注入文本标题明确是末尾动态补充上下文。
  - 增加测试：模拟 contribution dict，确认后置字段为 `placement="tail"`、`priority=-100`。

---

### Task 1: Add Failing Tests For Tail Placement Semantics

**Files:**
- Modify: `test_auto_inject.py:22-25`
- Modify: `test_auto_inject.py:70`
- Read/Reference: `auto_inject.py:485-527`, `auto_inject.py:742-754`

- [ ] **Step 1: Import `_build_injection_text` in the test file**

Change the import block in `test_auto_inject.py` to:

```python
from send_to.auto_inject import (
    _build_injection_text,
    _format_actor_label,
    _resolve_effective_person_id,
)
```

- [ ] **Step 2: Add failing tests at the end of `test_auto_inject.py`**

Append:

```python

def test_injection_text_declares_tail_dynamic_context():
    """自动注入文本应声明自己是末尾动态补充上下文。"""
    text = _build_injection_text(
        [
            {
                "scope_label": "群聊",
                "stream_name": "测试群",
                "timeline": "[2026-05-26 12:00:00] 其他群成员(阿A / id=a / person_id=person_a): hello",
            }
        ],
        is_kfc=True,
    )

    assert text.startswith("## 本轮末尾动态补充上下文")
    assert "不是用户新消息" in text
    assert "不是系统规则" in text


def test_tail_context_contribution_payload_uses_low_priority_tail_metadata():
    """send_to 的 context contribution 应带低优先级尾部放置元数据。"""
    contribution = {
        "source": "send_to.send_to_auto_context_inject",
        "owner": "notice",
        "scope": "turn",
        "priority": -100,
        "placement": "tail",
        "ttl_turns": 1,
        "content": "## 本轮末尾动态补充上下文\n正文",
    }

    assert contribution["owner"] == "notice"
    assert contribution["scope"] == "turn"
    assert contribution["priority"] == -100
    assert contribution["placement"] == "tail"
    assert contribution["ttl_turns"] == 1
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
python -m pytest -c /dev/null ./test_auto_inject.py -v
```

Expected:

```text
FAILED test_auto_inject.py::test_injection_text_declares_tail_dynamic_context
AssertionError: assert False
```

The contribution metadata test may pass immediately because it only documents the desired payload shape. The required red failure is the `_build_injection_text` title test.

---

### Task 2: Implement Tail Dynamic Context Text And Metadata

**Files:**
- Modify: `auto_inject.py:507-527`
- Modify: `auto_inject.py:742-754`
- Test: `test_auto_inject.py`

- [ ] **Step 1: Change `_build_injection_text` title and explanation**

In both KFC and non-KFC return strings inside `_build_injection_text`, replace the opening text with this shared wording:

```python
            "## 本轮末尾动态补充上下文\n"
            "以下内容由 send_to 在本轮 prompt 尾部追加，仅作为参考上下文；"
            "它不是用户新消息，也不是系统规则。\n"
            "以下是目标用户在其他聊天流中的近期对话，供你参考。\n"
```

Keep the existing sender attribution instructions and body interpolation after that wording.

- [ ] **Step 2: Add tail placement metadata to context contribution**

In `auto_inject.py`, change the contribution dict appended at `context_contributions.append(...)` to:

```python
                {
                    "source": "send_to.send_to_auto_context_inject",
                    "owner": "notice",
                    "scope": "turn",
                    "priority": -100,
                    "placement": "tail",
                    "ttl_turns": 1,
                    "content": injection_text,
                }
```

- [ ] **Step 3: Run tests to verify GREEN**

Run:

```bash
python -m pytest -c /dev/null ./test_auto_inject.py -v
```

Expected:

```text
4 passed
```

---

### Task 3: Verify Version Stability And Publish

**Files:**
- Check: `manifest.json:3`
- Test: `test_auto_inject.py`
- Command: `mpdt market publish .`

- [ ] **Step 1: Run focused tests**

Run:

```bash
python -m pytest -c /dev/null ./test_auto_inject.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 2: Run syntax check**

Run:

```bash
python -m py_compile auto_inject.py context_lookup.py action.py relay.py tools.py plugin.py
```

Expected: command exits with status 0 and no output.

- [ ] **Step 3: Confirm manifest version unchanged**

Run:

```bash
grep -n '"version"' manifest.json
```

Expected:

```text
3:  "version": "3.0.0",
```

- [ ] **Step 4: Publish with UTF-8 environment**

Run:

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 mpdt market publish .
```

Expected:

```text
[OK] Publish complete: https://github.com/fuilyha56-wq/send_to/releases/tag/v3.0.0
```

---

## Self-Review

- Spec coverage: covers tail placement metadata, title wording, fallback append behavior, version unchanged, publish.
- Placeholder scan: no TBD/TODO/implement later placeholders remain.
- Type consistency: tests import existing private functions and assert concrete strings/metadata used by implementation.
