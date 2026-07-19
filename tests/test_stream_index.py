"""send_to 跨流摘要索引回归测试。"""

from __future__ import annotations

from dataclasses import asdict
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
sys.path.insert(0, str(ROOT_DIR))

from send_to.config import SendToConfig  # noqa: E402
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
                        timestamp=1.0,
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