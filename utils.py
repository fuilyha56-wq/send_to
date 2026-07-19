"""send_to 插件公共工具函数。

整合各模块中重复出现的工具逻辑：
- 配置读取：get_config
- 文本处理：trim_text、content_preview、normalize_text
- 数值约束：clamp_int
- 时间格式化：format_time
- 异步锁管理：get_or_create_lock
- 黑白名单检查：check_list_membership
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from .config import SendToConfig


async def send_streaming_text(request: Any) -> str:
    """以流式模式发送 LLM 请求并消费全部事件，返回最终可见文本。"""

    response = await request.send(stream=True)
    async for _event in response.stream_events():
        pass
    return str(response.message or "").strip()


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
