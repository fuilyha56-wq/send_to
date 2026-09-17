"""send_to 跨流摘要索引回归测试。"""

from __future__ import annotations

from dataclasses import asdict
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
sys.path.insert(0, str(ROOT_DIR))

from send_to.config import SendToConfig  # noqa: E402
from src.core.prompt import SystemReminderInsertType  # noqa: E402
from send_to import stream_index  # noqa: E402


@pytest.mark.asyncio
async def test_auto_summary_empty_response_keeps_complete_pending_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回空文本时应保留完整批次，供后续消息触发重试。"""

    config = SendToConfig()
    config.index.auto_summary_batch_size = 2
    plugin = SimpleNamespace(plugin_name="send_to", config=config)
    stream_id = "stream-1"
    # 保留期默认 3 天，时间戳必须取当前时间，否则记录会被当作过期清理
    now_ts = time.time()
    stored: dict[str, dict[str, Any]] = {
        f"pending_{stream_id}": {
            "messages": [
                asdict(
                    stream_index.PendingMessageRecord(
                        message_id="old",
                        sender_name="用户",
                        text="旧消息",
                        direction="inbound",
                        stream_name="测试流",
                        platform="qq",
                        chat_type="private",
                        timestamp=now_ts,
                    )
                )
            ]
        }
    }

    async def load_json(_plugin_name: str, key: str) -> dict[str, Any] | None:
        return stored.get(key)

    async def save_json(_plugin_name: str, key: str, value: dict[str, Any]) -> None:
        stored[key] = value

    request = SimpleNamespace(add_payload=lambda _payload: None, send=_send_empty_stream)
    monkeypatch.setattr(stream_index.storage_api, "load_json", load_json)
    monkeypatch.setattr(stream_index.storage_api, "save_json", save_json)
    monkeypatch.setattr(stream_index, "should_collect_message", lambda *_args: True)
    monkeypatch.setattr(stream_index, "_extract_target_id", lambda _message: "10001")
    monkeypatch.setattr(
        stream_index,
        "_message_to_pending_record",
        lambda _message, _direction: stream_index.PendingMessageRecord(
            message_id="new",
            sender_name="Bot",
            text="新消息",
            direction="outbound",
            stream_name="测试流",
            platform="qq",
            chat_type="private",
            timestamp=2.0,
        ),
    )
    monkeypatch.setattr(stream_index.llm_api, "get_model_set_by_task", lambda _task: [])
    monkeypatch.setattr(stream_index.llm_api, "create_llm_request", lambda **_kwargs: request)

    changed = await stream_index.collect_message_for_auto_summary(
        plugin,
        SimpleNamespace(stream_id=stream_id, chat_type="private"),
        direction="outbound",
    )

    assert changed is False
    assert [item["message_id"] for item in stored[f"pending_{stream_id}"]["messages"]] == [
        "old",
        "new",
    ]
    assert f"summary_{stream_id}" not in stored


class EmptyStreamingResponseStub:
    """模拟不产生可见正文的流式响应。"""

    message = ""

    async def stream_events(self) -> AsyncIterator[SimpleNamespace]:
        """返回不包含任何事件的空流。"""

        if False:
            yield SimpleNamespace(text_delta="")


async def _send_empty_stream(*, stream: bool) -> EmptyStreamingResponseStub:
    """构造空流响应并校验调用方启用了流式模式。"""

    assert stream is True
    return EmptyStreamingResponseStub()


@pytest.mark.asyncio
async def test_auto_summary_llm_exception_keeps_new_message_in_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 调用抛异常（如读超时）时，本轮收集的消息必须落盘等待重试。"""

    config = SendToConfig()
    # batch_size=1 确保单条消息即触发 LLM 调用，让异常路径真正执行
    config.index.auto_summary_batch_size = 1
    plugin = SimpleNamespace(plugin_name="send_to", config=config)
    stream_id = "stream-1"
    stored: dict[str, dict[str, Any]] = {}

    async def load_json(_plugin_name: str, key: str) -> dict[str, Any] | None:
        return stored.get(key)

    async def save_json(_plugin_name: str, key: str, value: dict[str, Any]) -> None:
        stored[key] = value

    async def send(*, stream: bool) -> EmptyStreamingResponseStub:
        raise TimeoutError("simulated read timeout")

    request = SimpleNamespace(add_payload=lambda _payload: None, send=send)
    monkeypatch.setattr(stream_index.storage_api, "load_json", load_json)
    monkeypatch.setattr(stream_index.storage_api, "save_json", save_json)
    monkeypatch.setattr(stream_index, "should_collect_message", lambda *_args: True)
    monkeypatch.setattr(stream_index, "_extract_target_id", lambda _message: "10001")
    monkeypatch.setattr(
        stream_index,
        "_message_to_pending_record",
        lambda _message, _direction: stream_index.PendingMessageRecord(
            message_id="new",
            sender_name="Bot",
            text="新消息",
            direction="outbound",
            stream_name="测试流",
            platform="qq",
            chat_type="private",
            timestamp=2.0,
        ),
    )
    monkeypatch.setattr(stream_index.llm_api, "get_model_set_by_task", lambda _task: [])
    monkeypatch.setattr(stream_index.llm_api, "create_llm_request", lambda **_kwargs: request)

    # 旧代码在异常时直接向上抛出，导致 append 进内存的 pending_record 永远不会被保存
    await stream_index.collect_message_for_auto_summary(
        plugin,
        SimpleNamespace(stream_id=stream_id, chat_type="private"),
        direction="outbound",
    )

    assert [item["message_id"] for item in stored[f"pending_{stream_id}"]["messages"]] == [
        "new",
    ]


def _make_summary_record(
    stream_id: str,
    summary: str,
    updated_at: str,
) -> stream_index.StreamSummaryRecord:
    return stream_index.StreamSummaryRecord(
        stream_id=stream_id,
        stream_name=f"流-{stream_id}",
        platform="qq",
        chat_type="private",
        target_id="10001",
        summary=summary,
        updated_at=updated_at,
    )


def test_build_actor_reminder_render_order_is_stable() -> None:
    """渲染顺序按 stream_id 稳定排序，不随 updated_at 抖动。"""

    config = SendToConfig()
    plugin = SimpleNamespace(plugin_name="send_to", config=config)
    newer = _make_summary_record("aaa", "新摘要", "2026-09-12T10:00:00+00:00")
    older = _make_summary_record("zzz", "旧摘要", "2026-09-01T10:00:00+00:00")

    # 传入 updated_at 倒序（list_summary_records 的输出顺序）
    text = stream_index.build_actor_reminder(plugin, [newer, older], 10)

    assert text.index("aaa") < text.index("zzz")


@pytest.mark.asyncio
async def test_sync_actor_reminder_dynamic_insert_and_skip_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reminder 应以 DYNAMIC 插入，且内容未变化时不重复写 store。"""

    config = SendToConfig()
    config.index.inject_summary_reminder = True
    plugin = SimpleNamespace(plugin_name="send_to", config=config)
    record = _make_summary_record("sid-1", "摘要A", "2026-09-12T10:00:00+00:00")

    calls: list[tuple[str, str, str, SystemReminderInsertType | None]] = []

    def fake_add(bucket, name, content, insert_type=None, consume=None):
        calls.append((bucket, name, content, insert_type))

    async def fake_list_records(_plugin):
        return [record]

    monkeypatch.setattr(stream_index.prompt_api, "add_system_reminder", fake_add)
    monkeypatch.setattr(stream_index, "list_summary_records", fake_list_records)
    monkeypatch.setattr(stream_index, "_last_synced_reminder_text", None)

    await stream_index.sync_actor_reminder(
        plugin, current_chat_type="private", current_stream_id="sid-1"
    )
    assert len(calls) == 1
    assert calls[0][3] == SystemReminderInsertType.DYNAMIC

    # 状态未变化：不重复写 store
    await stream_index.sync_actor_reminder(
        plugin, current_chat_type="private", current_stream_id="sid-1"
    )
    assert len(calls) == 1

    # 摘要内容变化：重新写入
    record.summary = "摘要B"
    await stream_index.sync_actor_reminder(
        plugin, current_chat_type="private", current_stream_id="sid-1"
    )
    assert len(calls) == 2
    assert "摘要B" in calls[1][2]