"""Regression tests for send_to auto injection sender attribution."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

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
