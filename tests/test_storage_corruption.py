"""send_to 存储文件损坏自愈回归测试。

宿主 JSONStore.save 先清空文件再写入（非原子），进程在写入中途被杀会留下
0 字节文件，之后每次 json.loads 都抛 JSONDecodeError。验证插件端将其降级为
告警 + 视同不存在，并自动重建该键。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
sys.path.insert(0, str(ROOT_DIR))

from send_to import daily_memory  # noqa: E402
from send_to import utils as utils_module  # noqa: E402


@pytest.mark.asyncio
async def test_safe_load_json_returns_none_on_corrupt_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_json 抛 JSONDecodeError / UnicodeDecodeError 时应返回 None 而非向上抛。"""

    plugin = SimpleNamespace(plugin_name="send_to")

    async def load_json_corrupt(_plugin_name: str, _key: str) -> dict[str, Any]:
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(utils_module.storage_api, "load_json", load_json_corrupt)
    assert await utils_module.safe_load_json(plugin, "daily_state_s1") is None

    async def load_json_garbage(_plugin_name: str, _key: str) -> dict[str, Any]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(utils_module.storage_api, "load_json", load_json_garbage)
    assert await utils_module.safe_load_json(plugin, "daily_state_s1") is None

    async def load_json_ok(_plugin_name: str, _key: str) -> dict[str, Any]:
        return {"stream_id": "s1"}

    monkeypatch.setattr(utils_module.storage_api, "load_json", load_json_ok)
    assert await utils_module.safe_load_json(plugin, "daily_state_s1") == {
        "stream_id": "s1"
    }


@pytest.mark.asyncio
async def test_register_bot_message_rebuilds_corrupt_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状态文件损坏时 register_bot_message 不应抛异常，而应重建并保存新状态。"""

    config = utils_module.SendToConfig()
    config.daily_memory.enabled = True
    plugin = SimpleNamespace(plugin_name="send_to", config=config)
    stream_id = "0a4bfec7"

    async def load_json(_plugin_name: str, key: str) -> dict[str, Any]:
        # 模拟进程中断留下的 0 字节状态文件
        raise json.JSONDecodeError("Expecting value", "", 0)

    saved: dict[str, dict[str, Any]] = {}

    async def save_json(_plugin_name: str, key: str, value: dict[str, Any]) -> None:
        saved[key] = value

    monkeypatch.setattr(daily_memory.storage_api, "load_json", load_json)
    monkeypatch.setattr(daily_memory.storage_api, "save_json", save_json)

    message = SimpleNamespace(
        chat_type="group",
        stream_id=stream_id,
        platform="qq",
        extra={"group_id": "10001", "group_name": "测试群"},
    )
    await daily_memory.register_bot_message(plugin, message)

    state = saved[f"daily_state_{stream_id}"]
    assert state["stream_id"] == stream_id
    assert state["group_id"] == "10001"
    assert state["group_name"] == "测试群"
    assert state["round_count"] == 1
    assert state["last_event_direction"] == "outbound"
