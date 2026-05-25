"""send_to 跨流转告逻辑。"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from src.app.plugin_system.api import message_api, stream_api
from src.app.plugin_system.types import Message, MessageType
from src.kernel.logger import get_logger

from .config import SendToConfig

logger = get_logger("send_to.relay")


def trim_relay_content(content: str, max_chars: int) -> str:
    """按上限截断转告内容。"""

    if max_chars <= 0 or len(content) <= max_chars:
        return content
    if max_chars <= 16:
        return content[:max_chars]
    return content[: max_chars - 16].rstrip() + "...(已截断)"


def format_context_messages(messages: list[dict]) -> str:
    """将消息列表格式化为带 sender_id 的可读字符串。"""

    now_ts = time.time()
    lines: list[str] = []
    for msg in messages:
        ts = float(msg.get("time") or 0.0)
        delta = max(0.0, now_ts - ts)
        if delta < 60:
            ts_text = "刚刚"
        elif delta < 3600:
            ts_text = f"{int(delta // 60)}分钟前"
        elif delta < 86400:
            ts_text = f"{int(delta // 3600)}小时前"
        else:
            ts_text = f"{int(delta // 86400)}天前"

        sender_name = str(msg.get("sender_name") or "未知用户")
        sender_id = str(msg.get("sender_id") or "")
        sender_label = f"{sender_name}({sender_id})" if sender_id and sender_id != sender_name else sender_name
        content = str(msg.get("processed_plain_text") or msg.get("content") or "")
        lines.append(f"[{ts_text}] {sender_label}: {content}")
    return "\n".join(lines)


async def resolve_target_stream(
    *,
    target_stream_id: str,
    target_platform: str,
    target_user_id: str,
    target_group_id: str,
) -> Any | str:
    """解析或创建目标聊天流。"""

    if target_stream_id:
        try:
            return await stream_api.get_or_create_stream(
                stream_id=target_stream_id,
                platform=target_platform,
                user_id=target_user_id,
                group_id=target_group_id,
            )
        except Exception as error:
            return f"获取目标流失败: {error}"

    if not target_platform:
        return "未提供 stream_id 时，必须提供 target_platform。"

    chat_type = "group" if target_group_id else "private"
    try:
        return await stream_api.get_or_create_stream(
            platform=target_platform,
            user_id=target_user_id,
            group_id=target_group_id,
            chat_type=chat_type,
        )
    except Exception as error:
        return f"构造目标流失败: {error}"


async def relay_intent(
    *,
    plugin: Any,
    chat_stream: Any,
    target_stream_id: str,
    relay_content: str,
    context_message_count: int,
    opening_hint: str,
    target_platform: str,
    target_user_id: str,
    target_group_id: str,
) -> tuple[bool, str]:
    """把跨流转告意图注入目标流并启动目标流。"""

    config = plugin.config if isinstance(plugin.config, SendToConfig) else SendToConfig()
    if not config.relay.enabled:
        return False, "跨流转告功能已在配置中禁用"

    if not relay_content or not relay_content.strip():
        return False, "relay_content 不能为空"

    context_count = max(0, min(int(context_message_count), int(config.relay.include_context_messages_max)))
    max_chars = max(64, int(config.relay.max_relay_chars))
    trimmed_content = trim_relay_content(relay_content.strip(), max_chars)

    origin_stream_id = chat_stream.stream_id
    target = await resolve_target_stream(
        target_stream_id=target_stream_id.strip(),
        target_platform=(target_platform.strip() or config.relay.default_target_platform),
        target_user_id=target_user_id.strip(),
        target_group_id=target_group_id.strip(),
    )
    if isinstance(target, str):
        return False, target

    if target.stream_id == origin_stream_id and not config.relay.allow_self_relay:
        return False, "目标流就是当前流，禁止自我转告。"

    intent_id = uuid4().hex[:16]
    origin_stream_name = chat_stream.stream_name or origin_stream_id[:8]
    origin_platform = chat_stream.platform or ""
    origin_chat_type = chat_stream.chat_type or ""

    extra_kwargs: dict[str, Any] = {
        "_send_to_relay_intent_id": intent_id,
        "_send_to_relay_origin_stream_id": origin_stream_id,
        "_send_to_relay_origin_stream_name": origin_stream_name,
        "_send_to_relay_origin_chat_type": origin_chat_type,
        "_send_to_relay_origin_platform": origin_platform,
        "_send_to_relay_created_at": time.time(),
    }
    if opening_hint.strip():
        extra_kwargs["_send_to_relay_opening_hint"] = opening_hint.strip()

    virtual_sender_id = "0"
    if target.chat_type == "private":
        if target_user_id.strip():
            virtual_sender_id = target_user_id.strip()
            extra_kwargs["target_user_id"] = target_user_id.strip()
    elif target.chat_type == "group" and target_group_id.strip():
        extra_kwargs["target_group_id"] = target_group_id.strip()

    context_messages_text = ""
    if context_count > 0:
        try:
            recent_messages = await message_api.get_recent_messages(
                stream_id=origin_stream_id,
                hours=24,
                limit=context_count,
                limit_mode="latest",
                filter_bot=False,
            )
            if recent_messages:
                context_messages_text = format_context_messages(recent_messages)
        except Exception as error:
            logger.warning(f"[relay] 获取原始上下文消息失败: {error}")

    full_content_lines: list[str] = []
    if context_messages_text:
        full_content_lines.extend([
            f"【来自「{origin_stream_name}」的记忆凭证】",
            context_messages_text,
            "",
            "---",
            "",
        ])
    full_content_lines.append(trimmed_content)
    if opening_hint.strip():
        full_content_lines.extend(["", f"[心理暗示: {opening_hint.strip()}]"])
    full_content = "\n".join(full_content_lines)

    virtual_message = Message(
        message_id=f"send_to_relay_{intent_id}",
        platform=target.platform or origin_platform or config.relay.default_target_platform,
        stream_id=target.stream_id,
        sender_id=virtual_sender_id,
        sender_name=f"来自「{origin_stream_name}」的转告",
        sender_role="system",
        content=full_content,
        processed_plain_text=full_content,
        message_type=MessageType.TEXT,
        chat_type=target.chat_type or "",
        time=time.time(),
        **extra_kwargs,
    )

    try:
        target.context.add_unread_message(virtual_message)
        from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager

        await get_stream_loop_manager().start_stream_loop(target.stream_id)
    except Exception as error:
        logger.error(f"[relay] 注入虚拟消息失败: {error}", exc_info=True)
        return False, f"注入虚拟消息失败: {error}"

    target_label = target.stream_name or target.stream_id[:8]
    logger.info(f"[relay] 转告完成 {origin_stream_id[:8]} -> {target.stream_id[:8]}")
    return True, f"已成功将意图转告至「{target_label}」，目标流会自行续接。"
