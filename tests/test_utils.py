"""send_to 公共工具回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
sys.path.insert(0, str(ROOT_DIR))

from send_to.utils import send_streaming_text  # noqa: E402


class StreamingResponseStub:
    """模拟消费后才填充 message 的框架流式响应。"""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.message: str | None = None

    async def stream_events(self) -> AsyncIterator[SimpleNamespace]:
        """逐片产出文本，并在消费结束后形成最终正文。"""

        collected: list[str] = []
        for chunk in self._chunks:
            collected.append(chunk)
            yield SimpleNamespace(text_delta=chunk)
        self.message = "".join(collected)


@pytest.mark.asyncio
async def test_send_streaming_text_consumes_stream_and_returns_message() -> None:
    """工具应请求流式响应、消费全部事件并返回最终正文。"""

    response = StreamingResponseStub(["流式", "摘要"])
    stream_options: list[bool] = []

    async def send(*, stream: bool) -> StreamingResponseStub:
        stream_options.append(stream)
        return response

    request = SimpleNamespace(send=send)

    result = await send_streaming_text(request)

    assert stream_options == [True]
    assert result == "流式摘要"