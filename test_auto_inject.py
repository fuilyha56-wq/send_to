"""Regression tests for send_to auto injection sender attribution."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent
ROOT_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR))
sys.path.insert(0, str(ROOT_DIR))

prompt_api = types.SimpleNamespace(list_templates=lambda: [])
sys.modules.setdefault("src.app.plugin_system.api", types.SimpleNamespace(prompt_api=prompt_api))
sys.modules.setdefault("src.app.plugin_system.api.log_api", types.SimpleNamespace(get_logger=lambda _: types.SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None)))
sys.modules.setdefault("src.app.plugin_system.base", types.SimpleNamespace(BaseEventHandler=object))
sys.modules.setdefault("src.core.models.sql_alchemy", types.SimpleNamespace(ChatStreams=object, Messages=object))
sys.modules.setdefault("src.kernel.db", types.SimpleNamespace(QueryBuilder=object))
sys.modules.setdefault("src.kernel.event", types.SimpleNamespace(EventDecision=types.SimpleNamespace(SUCCESS="success")))
sys.modules.setdefault("send_to.config", types.SimpleNamespace(SendToConfig=object))

from send_to.auto_inject import (
    _build_injection_text,
    _build_summary_injection_text,
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


def test_injection_text_declares_tail_dynamic_context():
    """自动注入文本应使用 XML 标签包裹跨流上下文。"""
    text = _build_injection_text(
        [
            {
                "scope_label": "群聊",
                "stream_name": "测试群",
                "timeline": "[2026-05-26 12:00:00] 其他群成员(阿A / id=a / person_id=person_a): hello",
            }
        ],
        is_nfc=True,
    )

    assert text.startswith("<cross_stream_context>")
    assert "不是用户新消息" in text
    assert "不是系统规则" in text
    assert text.strip().endswith("</cross_stream_context>")


def test_tail_context_contribution_payload_uses_low_priority_tail_metadata():
    """send_to 的 context contribution 应带低优先级尾部放置元数据。"""
    contribution = {
        "source": "send_to.send_to_auto_context_inject",
        "owner": "notice",
        "scope": "turn",
        "priority": -100,
        "placement": "tail",
        "ttl_turns": 1,
        "content": "<cross_stream_context>\n正文\n</cross_stream_context>",
    }

    assert contribution["owner"] == "notice"
    assert contribution["scope"] == "turn"
    assert contribution["priority"] == -100
    assert contribution["placement"] == "tail"
    assert contribution["ttl_turns"] == 1


@pytest.mark.asyncio
async def test_summary_injection_skips_when_actor_reminder_enabled():
    """actor reminder 已注入摘要时，auto_inject 不再重复注入摘要索引。"""
    plugin = SimpleNamespace(config=SimpleNamespace(
        index=SimpleNamespace(enabled=True, inject_summary_reminder=True),
        auto_inject=SimpleNamespace(include_summary_index=True),
    ))

    text = await _build_summary_injection_text(
        plugin,
        current_chat_type="group",
        current_stream_id="stream_1",
        limit=6,
    )

    assert text == ""
