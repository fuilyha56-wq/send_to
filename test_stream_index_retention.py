"""send_to 实时跨流索引保留期测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.send_to.config import SendToConfig
from plugins.send_to import stream_index


class MemoryStorage:
    """用于测试过期清理的内存存储。"""

    def __init__(self, values: dict[str, dict[str, Any]]) -> None:
        self.values = values
        self.deleted: list[str] = []
        self.saved: dict[str, dict[str, Any]] = {}

    async def list_json(self, store_name: str) -> list[str]:
        """列出所有测试键。"""
        return list(self.values)

    async def load_json(self, store_name: str, name: str) -> dict[str, Any] | None:
        """读取测试值。"""
        return self.values.get(name)

    async def save_json(
        self,
        store_name: str,
        name: str,
        data: dict[str, Any],
    ) -> None:
        """记录保存后的测试值。"""
        self.values[name] = data
        self.saved[name] = data

    async def delete_json(self, store_name: str, name: str) -> bool:
        """删除测试值。"""
        existed = name in self.values
        self.values.pop(name, None)
        if existed:
            self.deleted.append(name)
        return existed


def _plugin(retention_days: int = 3) -> SimpleNamespace:
    """创建带真实 SendToConfig 的最小插件替身。"""
    config = SendToConfig()
    config.index.retention_days = retention_days
    return SimpleNamespace(plugin_name="send_to", config=config)


def _summary(stream_id: str, updated_at: str) -> dict[str, Any]:
    """创建摘要存储值。"""
    return {
        "stream_id": stream_id,
        "stream_name": stream_id,
        "platform": "qq",
        "chat_type": "group",
        "target_id": "1000",
        "summary": "测试摘要",
        "updated_at": updated_at,
    }


@pytest.mark.asyncio
async def test_cleanup_removes_expired_realtime_data_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """过期摘要和缓冲应删除，每日记忆与新数据应保留。"""
    now = datetime(2026, 7, 19, 12, tzinfo=UTC).timestamp()
    storage = MemoryStorage(
        {
            "summary_old": _summary(
                "old",
                datetime(2026, 7, 15, 12, tzinfo=UTC).isoformat(),
            ),
            "summary_new": _summary(
                "new",
                datetime(2026, 7, 18, 12, tzinfo=UTC).isoformat(),
            ),
            "pending_old": {
                "messages": [{"text": "旧消息", "timestamp": now - 4 * 86400}],
            },
            "daily_memory_old_2026-07-15": {"summary": "每日记忆"},
        }
    )
    monkeypatch.setattr(stream_index, "storage_api", storage)
    monkeypatch.setattr(stream_index.time, "time", lambda: now)

    result = await stream_index.cleanup_expired_stream_index(_plugin())

    assert result == (1, 1)
    assert "summary_old" in storage.deleted
    assert "pending_old" in storage.deleted
    assert "summary_new" in storage.values
    assert "daily_memory_old_2026-07-15" in storage.values


@pytest.mark.asyncio
async def test_pending_messages_keep_only_unexpired_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """混合缓冲应只保存三天内的新消息。"""
    now = datetime(2026, 7, 19, 12, tzinfo=UTC).timestamp()
    storage = MemoryStorage(
        {
            "pending_stream-1": {
                "messages": [
                    {"text": "旧消息", "timestamp": now - 4 * 86400},
                    {"text": "新消息", "timestamp": now - 86400},
                ],
            },
        }
    )
    monkeypatch.setattr(stream_index, "storage_api", storage)
    monkeypatch.setattr(stream_index.time, "time", lambda: now)

    records = await stream_index._load_pending_messages(_plugin(), "stream-1")

    assert [record.text for record in records] == ["新消息"]
    assert len(storage.saved["pending_stream-1"]["messages"]) == 1


@pytest.mark.asyncio
async def test_zero_retention_disables_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """保留天数为零时应禁用过期清理。"""
    storage = MemoryStorage(
        {
            "summary_old": _summary("old", "2020-01-01T00:00:00+00:00"),
        }
    )
    monkeypatch.setattr(stream_index, "storage_api", storage)

    result = await stream_index.cleanup_expired_stream_index(_plugin(0))

    assert result == (0, 0)
    assert not storage.deleted
