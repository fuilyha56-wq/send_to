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

from send_to.utils import (  # noqa: E402
    override_model_set_tokens,
    send_streaming_text,
    strip_think_blocks,
)


class StreamingResponseStub:
    """模拟消费后才填充 message 的框架流式响应。"""

    def __init__(
        self,
        chunks: list[str],
        *,
        reasoning_content: str | None = None,
        stop_reason: str | None = None,
    ) -> None:
        self._chunks = chunks
        self.message: str | None = None
        self.reasoning_content = reasoning_content
        self.stop_reason = stop_reason

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


@pytest.mark.asyncio
async def test_send_streaming_text_strips_inline_think_block() -> None:
    """混入正文的思考链应被剥离，只保留真实正文。"""

    response = StreamingResponseStub(["<think>让我想想", "怎么总结</think>真正的摘要"])

    async def send(*, stream: bool) -> StreamingResponseStub:
        return response

    result = await send_streaming_text(SimpleNamespace(send=send))

    assert result == "真正的摘要"


@pytest.mark.asyncio
async def test_send_streaming_text_returns_empty_when_reasoning_ate_budget() -> None:
    """思考链耗尽 max_tokens（正文为空）时应返回空串而非抛错。"""

    response = StreamingResponseStub(
        [],
        reasoning_content="一段很长很长的思考链" * 100,
        stop_reason="length",
    )

    async def send(*, stream: bool) -> StreamingResponseStub:
        return response

    result = await send_streaming_text(SimpleNamespace(send=send))

    assert result == ""


def test_strip_think_blocks_variants() -> None:
    """剥离逻辑应覆盖成对、未闭合、悬空闭标签三种形态。"""

    assert strip_think_blocks("<think>推理</think>正文") == "正文"
    assert strip_think_blocks("<THINKING>推理</THINKING>正文") == "正文"
    assert strip_think_blocks("<think>一</think>A<think>二</think>B") == "AB"
    assert strip_think_blocks("正文<think>被截断的推理……") == "正文"
    assert strip_think_blocks("只有思考</think>正文") == "正文"
    assert strip_think_blocks("没有标签的普通文本") == "没有标签的普通文本"
    assert strip_think_blocks("") == ""


def test_override_model_set_tokens_floor_semantics() -> None:
    """max_tokens 下限应只抬升不降级，且不改动原模型集。"""

    model_set = [
        {"name": "a", "max_tokens": 800},
        {"name": "b", "max_tokens": 32768},
        {"name": "c"},
    ]
    adjusted = override_model_set_tokens(model_set, 4096)

    assert [entry["max_tokens"] for entry in adjusted] == [4096, 32768, 4096]
    # 原配置不被污染
    assert model_set[0]["max_tokens"] == 800
    assert "max_tokens" not in model_set[2]

    # 下限为 0 / 非法值时原样返回
    assert override_model_set_tokens(model_set, 0) is model_set
    assert override_model_set_tokens(model_set, -1) is model_set