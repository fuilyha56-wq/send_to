"""send_to 插件公共工具函数。

整合各模块中重复出现的工具逻辑：
- 配置读取：get_config
- 文本处理：trim_text、content_preview、normalize_text、strip_think_blocks
- LLM 消费：send_streaming_text（含思考链剥离与空回诊断）
- 模型集调整：override_model_set_tokens
- 数值约束：clamp_int
- 时间格式化：format_time
- 异步锁管理：get_or_create_lock
- 黑白名单检查：check_list_membership
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from .config import SendToConfig

logger = get_logger("send_to.utils")

# 成对出现的思考链块（<think>…</think> / <thinking>…</thinking>）
_THINK_PAIRED_RE = re.compile(
    r"<(think|thinking)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
# 只有开标签没有闭标签：多见于输出被 max_tokens 截断在思考链内
_THINK_UNCLOSED_RE = re.compile(
    r"<(think|thinking)\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE
)
# 只有闭标签没有开标签：部分供应商只下发 </think>，闭标签之前的内容都是思考链
_THINK_DANGLING_CLOSE_RE = re.compile(
    r"\A.*?</(think|thinking)>", re.DOTALL | re.IGNORECASE
)


def strip_think_blocks(text: str) -> str:
    """剥离模型混入正文的思考链片段，只保留真正的正文。

    兼容三种形态：成对的 <think>…</think>、被截断产生的未闭合 <think>…、
    以及部分供应商只下发 </think> 的悬空闭标签。
    """

    if not text:
        return ""

    cleaned = _THINK_PAIRED_RE.sub("", text)
    cleaned = _THINK_UNCLOSED_RE.sub("", cleaned)
    cleaned = _THINK_DANGLING_CLOSE_RE.sub("", cleaned)
    return cleaned.strip()


def override_model_set_tokens(model_set: Any, max_tokens: int) -> Any:
    """为模型集设置 max_tokens 下限，按条目浅拷贝返回，不改动全局任务配置。

    每个条目取「原 max_tokens 与下限的较大者」：任务配置已经更大时不降级，
    非正数下限原样返回，表示完全沿用任务配置。
    """

    if not isinstance(max_tokens, int) or max_tokens <= 0:
        return model_set
    try:
        entries = []
        for entry in model_set:
            current = entry.get("max_tokens") if isinstance(entry, dict) else None
            effective = (
                max(max_tokens, current) if isinstance(current, int) else max_tokens
            )
            entries.append({**entry, "max_tokens": effective})
        return entries
    except TypeError:
        return model_set


async def send_streaming_text(request: Any) -> str:
    """以流式模式发送 LLM 请求并消费全部事件，返回最终可见文本。

    会剥离混入正文的思考链；若剥离后正文为空（典型原因是思考链耗尽
    max_tokens 预算，finish_reason=length），输出针对性诊断日志。
    """

    response = await request.send(stream=True)
    async for _event in response.stream_events():
        pass

    raw_message = str(response.message or "")
    message = strip_think_blocks(raw_message)
    if message:
        return message

    reasoning = str(getattr(response, "reasoning_content", None) or "").strip()
    stop_reason = getattr(response, "stop_reason", None)
    had_inline_think = "<think" in raw_message.lower() or "</think>" in raw_message.lower()

    if reasoning and stop_reason == "length":
        logger.error(
            f"LLM 正文为空：思考链（约 {len(reasoning)} 字符）耗尽了 max_tokens 预算，"
            "正文尚未开始输出即被截断（finish_reason=length）。"
            "请调大该任务的 max_tokens（思考链模型建议 ≥4096），或换用非思考模型。"
        )
    elif reasoning or had_inline_think:
        logger.warning(
            "LLM 只返回了思考链、没有正文"
            f"（inline_think={had_inline_think}, finish_reason={stop_reason or 'unknown'}），按空结果处理。"
        )
    elif stop_reason == "length":
        logger.error(
            "LLM 输出为空且 finish_reason=length：max_tokens 预算不足，请调大该任务的 max_tokens。"
        )
    return message


def get_config(plugin: Any) -> SendToConfig:
    """从插件实例读取配置，失败时回退默认配置。"""

    config = getattr(plugin, "config", None)
    if isinstance(config, SendToConfig):
        return config
    return SendToConfig()


def normalize_text(value: str | None) -> str:
    """清理文本参数。"""

    return str(value or "").strip()


def normalize_list(values: list[str]) -> set[str]:
    """规范化配置列表。"""

    return {str(item).strip().lower() for item in values if str(item).strip()}


def trim_text(text: str, max_chars: int, *, empty_raises: bool = False) -> str:
    """归一化并按上限截断文本。

    Args:
        text: 原始文本
        max_chars: 最大字符数，0 或负值表示不限
        empty_raises: 为 True 时空文本抛 ValueError；否则返回空串
    """

    normalized = "\n".join(
        line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()
    ).strip()
    if not normalized:
        if empty_raises:
            raise ValueError("文本不能为空")
        return ""

    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def content_preview(value: Any, max_chars: int) -> str:
    """生成消息正文预览。"""

    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    """将整数压入指定范围。"""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def format_time(value: Any) -> str:
    """格式化 Unix 时间戳。"""

    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def get_or_create_lock(
    locks_dict: dict[str, asyncio.Lock],
    key: str,
) -> asyncio.Lock:
    """按 key 取得异步锁。

    不主动清理锁字典：原清理逻辑（``len > 阈值`` 时删除 ``not v.locked()``
    的锁）存在竞态——A 协程拿到旧锁引用但未 acquire 时被清理，新来者会创建
    新锁，导致同 stream 出现两把锁、互斥失效。Lock 对象很轻量，且数量与
    stream 数量成正比（不会无限增长），不清理更安全。
    """

    lock = locks_dict.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks_dict[key] = lock
    return lock


def check_list_membership(
    target_id: str,
    list_type: str,
    id_list: list[str | int],
) -> bool:
    """根据黑白名单模式判断目标 ID 是否允许通过。

    Args:
        target_id: 待检测的群号或 QQ 号（字符串）
        list_type: "blacklist" 或 "whitelist"
        id_list: 黑/白名单列表

    Returns:
        True 表示允许（不被过滤），False 表示拒绝（被过滤掉）
    """

    normalized_list = {str(v).strip() for v in id_list}
    target = str(target_id).strip()

    if list_type == "whitelist":
        return target in normalized_list
    return target not in normalized_list
