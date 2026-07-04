"""send_to 用户与聊天流上下文查询逻辑。"""

from __future__ import annotations

import time
from typing import Any

from src.core.models.sql_alchemy import ChatStreams, Messages
from src.core.utils.user_query_helper import get_user_query_helper
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

from .config import SendToConfig
from .utils import (
    clamp_int,
    content_preview,
    format_time,
    get_config,
    normalize_list,
    normalize_text,
)

logger = get_logger("send_to.context_lookup")

_PRIVATE = "private"
_GROUP = "group"
_ALL = "all"
_MEMORY_PERSON_TYPE = "person"


def normalize_chat_scope(chat_scope: str, config: SendToConfig) -> str:
    """规范化聊天范围，按与请求 scope 的相关性降级。"""

    normalized = str(chat_scope or _ALL).strip().lower()
    if normalized not in {_PRIVATE, _GROUP, _ALL}:
        normalized = _ALL

    allowed = normalize_list(config.privacy.allowed_chat_scopes)
    if not allowed or normalized in allowed:
        return normalized

    # 按与请求 scope 的相关性降级：all 包含 group/private，group/private 互为远端
    fallback_order: dict[str, list[str]] = {
        _ALL: [_ALL, _PRIVATE, _GROUP],
        _PRIVATE: [_ALL, _PRIVATE, _GROUP],
        _GROUP: [_ALL, _GROUP, _PRIVATE],
    }
    for candidate in fallback_order.get(normalized, [_ALL, _PRIVATE, _GROUP]):
        if candidate in allowed:
            return candidate
    return ""


def ensure_platform_allowed(config: SendToConfig, platform: str) -> tuple[bool, str]:
    """检查平台黑白名单。"""

    normalized = normalize_text(platform).lower()
    allowlist = normalize_list(config.privacy.platform_allowlist)
    blocklist = normalize_list(config.privacy.platform_blocklist)
    if normalized in blocklist:
        return False, f"平台 {platform} 位于黑名单"
    if allowlist and normalized not in allowlist:
        return False, f"平台 {platform} 不在白名单"
    return True, ""


def ensure_group_allowed(config: SendToConfig, group_id: str) -> tuple[bool, str]:
    """检查群黑白名单。"""

    normalized = normalize_text(group_id).lower()
    if not normalized:
        return True, ""
    allowlist = normalize_list(config.privacy.group_allowlist)
    blocklist = normalize_list(config.privacy.group_blocklist)
    if normalized in blocklist:
        return False, f"群 {group_id} 位于黑名单"
    if allowlist and normalized not in allowlist:
        return False, f"群 {group_id} 不在白名单"
    return True, ""


def _identity_values(*, platform: str, user_id: str, person_id: str) -> set[str]:
    return {
        normalize_text(user_id).lower(),
        f"{normalize_text(platform).lower()}:{normalize_text(user_id).lower()}",
        normalize_text(person_id).lower(),
    }


def ensure_user_allowed(
    config: SendToConfig,
    *,
    platform: str,
    user_id: str,
    person_id: str,
) -> tuple[bool, str]:
    """检查用户黑白名单。"""

    identities = _identity_values(platform=platform, user_id=user_id, person_id=person_id)
    allowlist = normalize_list(config.privacy.user_allowlist)
    blocklist = normalize_list(config.privacy.user_blocklist)
    if identities & blocklist:
        return False, "目标用户位于黑名单"
    if allowlist and not identities & allowlist:
        return False, "目标用户不在白名单"
    return True, ""


def redact_person_id(value: str | None, config: SendToConfig) -> str | None:
    """按隐私配置隐藏 person_id。"""

    return value if config.privacy.expose_person_id else None


def redact_message_id(value: str | None, config: SendToConfig) -> str | None:
    """按隐私配置隐藏 message_id。"""

    return value if config.privacy.expose_message_id else None


def redact_stream_id(value: str | None, config: SendToConfig) -> str | None:
    """按隐私配置隐藏 stream_id。"""

    return value if config.privacy.expose_stream_id else None


def stream_to_dict(stream: Any, config: SendToConfig) -> dict[str, Any]:
    """将 ChatStreams 记录转为安全字典。"""

    return {
        "stream_id": redact_stream_id(str(getattr(stream, "stream_id", "") or ""), config),
        "platform": str(getattr(stream, "platform", "") or ""),
        "chat_type": str(getattr(stream, "chat_type", "") or ""),
        "group_id": getattr(stream, "group_id", None),
        "group_name": getattr(stream, "group_name", None),
        "last_active_time": getattr(stream, "last_active_time", None),
        "last_active_at": format_time(getattr(stream, "last_active_time", None)),
        "created_at": format_time(getattr(stream, "created_at", None)),
    }


def message_to_dict(
    message: Any,
    *,
    max_chars: int,
    current_person_id: str,
    config: SendToConfig,
) -> dict[str, Any]:
    """将 Messages 记录转为模型友好的上下文字典。"""

    sender_person_id = getattr(message, "person_id", None)
    sender_id = str(getattr(message, "sender_id", "") or "")
    is_bot = sender_person_id == "bot" or bool(sender_id) and sender_id == str(getattr(message, "bot_id", "") or "")
    if is_bot:
        sender_role = "bot"
    elif sender_person_id == current_person_id:
        sender_role = "target_user"
    else:
        sender_role = "other"

    text = getattr(message, "processed_plain_text", None) or getattr(message, "content", "")
    return {
        "message_id": redact_message_id(str(getattr(message, "message_id", "") or ""), config),
        "stream_id": redact_stream_id(str(getattr(message, "stream_id", "") or ""), config),
        "person_id": redact_person_id(sender_person_id, config),
        "sender_role": sender_role,
        "time": getattr(message, "time", None),
        "time_text": format_time(getattr(message, "time", None)),
        "message_type": str(getattr(message, "message_type", "") or ""),
        "reply_to": redact_message_id(getattr(message, "reply_to", None), config),
        "platform": getattr(message, "platform", None),
        "content": content_preview(text, max_chars=max_chars),
    }


def build_timeline(stream: Any, messages: list[dict[str, Any]], config: SendToConfig) -> str:
    """生成紧凑时间线文本。"""

    stream_name = str(getattr(stream, "group_name", "") or getattr(stream, "stream_id", "") or "未知会话")
    lines = [f"【{getattr(stream, 'chat_type', '')}/{stream_name}】"]
    for item in messages:
        message_id = item.get("message_id") or "hidden"
        lines.append(f"[{item['time_text']}] {item['sender_role']} ({message_id}): {item['content']}")
    return "\n".join(lines) if config.lookup.include_timeline_text else ""


async def resolve_user(
    *,
    platform: str,
    user_id: str,
    user_hint: str,
    config: SendToConfig,
) -> tuple[str, str, dict[str, Any] | None]:
    """解析目标用户。"""

    normalized_platform = normalize_text(platform)
    normalized_user_id = normalize_text(user_id)
    normalized_hint = normalize_text(user_hint)

    if not normalized_platform:
        raise ValueError("platform 不能为空")

    ok, reason = ensure_platform_allowed(config, normalized_platform)
    if not ok:
        raise ValueError(reason)

    helper = get_user_query_helper()
    resolved_user_id = normalized_user_id

    # 若 user_hint 是纯数字（QQ 号等），直接作为 user_id
    if not resolved_user_id and normalized_hint.isdigit():
        resolved_user_id = normalized_hint

    if not resolved_user_id and normalized_hint:
        resolved_user_id = await helper.resolve_user_id(normalized_platform, normalized_hint) or ""

    if not resolved_user_id and config.privacy.require_user_identity:
        raise ValueError("必须提供 user_id，或提供可唯一解析的 user_hint")
    if not resolved_user_id:
        resolved_user_id = normalized_hint

    person_id = helper.generate_person_id(normalized_platform, resolved_user_id)
    ok, reason = ensure_user_allowed(config, platform=normalized_platform, user_id=resolved_user_id, person_id=person_id)
    if not ok:
        raise ValueError(reason)

    person = await helper.person_crud.get_by(person_id=person_id)
    person_info = None
    if person is not None and config.privacy.include_person_profile:
        person_info = {
            "person_id": redact_person_id(person_id, config),
            "platform": getattr(person, "platform", normalized_platform),
            "user_id": getattr(person, "user_id", resolved_user_id),
            "nickname": getattr(person, "nickname", None),
            "cardname": getattr(person, "cardname", None),
            "short_impression": getattr(person, "short_impression", None),
            "attitude": getattr(person, "attitude", None),
            "interaction_count": getattr(person, "interaction_count", None),
            "last_interaction": getattr(person, "last_interaction", None),
            "last_interaction_at": format_time(getattr(person, "last_interaction", None)),
        }

    return resolved_user_id, person_id, person_info


async def find_user_streams(
    *,
    person_id: str,
    platform: str,
    chat_scope: str,
    group_id: str,
    max_streams: int,
    config: SendToConfig,
) -> list[Any]:
    """查找与目标用户相关的私聊/群聊流。"""

    scope = normalize_chat_scope(chat_scope, config)
    if not scope:
        return []

    normalized_group_id = normalize_text(group_id)
    stream_rows: list[Any] = []

    if scope in {_ALL, _PRIVATE}:
        private_rows = await QueryBuilder(ChatStreams).filter(person_id=person_id, chat_type=_PRIVATE).order_by("-last_active_time").limit(max_streams).all()
        stream_rows.extend(private_rows)

    if scope in {_ALL, _GROUP}:
        group_stream_ids: list[str] = []
        if normalized_group_id:
            filters: dict[str, Any] = {"group_id": normalized_group_id, "chat_type": _GROUP}
            if normalize_text(platform):
                filters["platform"] = platform
            explicit_group_rows = await QueryBuilder(ChatStreams).filter(**filters).order_by("-last_active_time").limit(max_streams).all()
            group_stream_ids.extend(str(getattr(row, "stream_id", "") or "") for row in explicit_group_rows)

        scan_limit = max_streams * config.lookup.candidate_stream_scan_multiplier
        user_message_rows = await QueryBuilder(Messages).filter(person_id=person_id, platform=platform).order_by("-time").limit(scan_limit).all()
        candidate_ids = list(dict.fromkeys(str(getattr(row, "stream_id", "") or "") for row in user_message_rows if getattr(row, "stream_id", None)))
        group_stream_ids.extend(candidate_ids)

        dedup_ids = [sid for sid in dict.fromkeys(group_stream_ids) if sid]
        if dedup_ids:
            group_rows = await QueryBuilder(ChatStreams).filter(stream_id__in=dedup_ids, chat_type=_GROUP).order_by("-last_active_time").limit(max_streams).all()
            stream_rows.extend(group_rows)

    dedup: dict[str, Any] = {}
    for stream in stream_rows:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid:
            continue
        ok, _ = ensure_group_allowed(config, str(getattr(stream, "group_id", "") or ""))
        if ok and sid not in dedup:
            dedup[sid] = stream

    return sorted(dedup.values(), key=lambda item: float(getattr(item, "last_active_time", 0.0) or 0.0), reverse=True)[:max_streams]


async def get_messages_for_stream(
    *,
    stream_id: str,
    person_id: str,
    limit: int,
    around_user: bool,
) -> list[Any]:
    """从指定聊天流抓取消息。"""

    if not around_user:
        rows = await QueryBuilder(Messages).filter(stream_id=stream_id).order_by("-time").limit(limit).all()
        return list(reversed(rows))

    target_rows = await QueryBuilder(Messages).filter(stream_id=stream_id, person_id=person_id).order_by("-time").limit(max(1, limit // 2)).all()
    if not target_rows:
        return []

    latest_target_time = max(float(getattr(row, "time", 0.0) or 0.0) for row in target_rows)
    before_limit = max(1, limit // 2)
    after_limit = max(0, limit - before_limit)
    before_rows = await QueryBuilder(Messages).filter(stream_id=stream_id, time__lte=latest_target_time).order_by("-time").limit(before_limit).all()
    after_rows = await QueryBuilder(Messages).filter(stream_id=stream_id, time__gt=latest_target_time).order_by("time").limit(after_limit).all()
    combined = list(reversed(before_rows))
    combined.extend(after_rows)
    return combined


async def lookup_user_memory(
    plugin: Any,
    *,
    platform: str,
    user_id: str,
    user_hint: str,
    query: str,
    top_n: int,
    include_archived: bool,
) -> tuple[bool, str | dict[str, Any]]:
    """检索用户长期记忆。"""

    config = get_config(plugin)
    try:
        resolved_user_id, person_id, person_info = await resolve_user(platform=platform, user_id=user_id, user_hint=user_hint, config=config)
    except ValueError as exc:
        return False, str(exc)

    normalized_top_n = clamp_int(top_n or config.lookup.memory_top_n_default, minimum=1, maximum=max(1, config.lookup.memory_top_n_max))
    normalized_query = normalize_text(query)
    should_include_archived = bool(include_archived or config.lookup.include_archived_default)
    items: list[dict[str, Any]] = []
    memory_available = False
    errors: list[str] = []

    try:
        from plugins.booku_memory.service import BookuMemoryService

        service = BookuMemoryService(plugin=plugin)
        memory_available = True
        search_jobs = [
            {
                "label": "person",
                "kwargs": {
                    "top_n": normalized_top_n,
                    "query_text": normalized_query or None,
                    "memory_type": _MEMORY_PERSON_TYPE,
                    "person_id": person_id,
                    "include_archived": should_include_archived,
                    "include_knowledge": False,
                    "include_related": config.lookup.include_related,
                },
            },
            {
                "label": "related",
                "kwargs": {
                    "top_n": normalized_top_n,
                    "query_text": normalized_query or resolved_user_id,
                    "memory_type": None,
                    "person_id": person_id,
                    "include_archived": should_include_archived,
                    "include_knowledge": False,
                    "include_related": config.lookup.include_related,
                },
            },
        ]
        if normalized_query and config.lookup.include_knowledge_when_query:
            search_jobs.append({"label": "query", "kwargs": {"top_n": normalized_top_n, "query_text": normalized_query, "memory_type": None, "person_id": None, "include_archived": should_include_archived, "include_knowledge": True, "include_related": False}})

        seen_ids: set[str] = set()
        for job in search_jobs:
            result = await service.search_memory_entries(**job["kwargs"])
            for item in result.get("items", []) or []:
                memory_id = str(item.get("id", "") or item.get("memory_id", "") or "")
                if not memory_id or memory_id in seen_ids:
                    continue
                seen_ids.add(memory_id)
                payload = dict(item)
                payload["matched_by"] = job["label"]
                items.append(payload)
                if len(items) >= normalized_top_n:
                    break
            if len(items) >= normalized_top_n:
                break
    except Exception as exc:
        logger.warning(f"Booku Memory 检索失败: {exc}", exc_info=True)
        errors.append(str(exc))

    return True, {
        "action": "send_to_lookup_user_memory",
        "ok": True,
        "memory_available": memory_available,
        "target": {"platform": normalize_text(platform), "user_id": resolved_user_id, "person_id": redact_person_id(person_id, config), "person_info": person_info},
        "query": normalized_query,
        "total": len(items),
        "items": items,
        "errors": errors,
        "hint": "如果 total 为 0，可继续调用 send_to_lookup_user_context 直接读取聊天流上下文。",
    }


async def lookup_user_context(
    plugin: Any,
    *,
    platform: str,
    user_id: str,
    user_hint: str,
    chat_scope: str,
    group_id: str,
    per_stream_limit: int,
    max_streams: int,
    around_user: bool,
    max_chars_per_message: int,
) -> tuple[bool, str | dict[str, Any]]:
    """抓取用户相关聊天流上下文。"""

    started_at = time.time()
    config = get_config(plugin)
    ok, reason = ensure_group_allowed(config, group_id)
    if not ok:
        return False, reason

    try:
        resolved_user_id, person_id, person_info = await resolve_user(platform=platform, user_id=user_id, user_hint=user_hint, config=config)
    except ValueError as exc:
        return False, str(exc)

    normalized_platform = normalize_text(platform)
    normalized_scope = normalize_chat_scope(chat_scope, config)
    if not normalized_scope:
        return False, "当前配置未允许任何聊天范围"

    normalized_max_streams = clamp_int(max_streams or config.lookup.streams_default, minimum=1, maximum=max(1, config.lookup.streams_max))
    normalized_limit = clamp_int(per_stream_limit or config.lookup.per_stream_limit_default, minimum=1, maximum=max(1, config.lookup.per_stream_limit_max))
    normalized_chars = clamp_int(max_chars_per_message or config.lookup.chars_per_message_default, minimum=40, maximum=max(40, config.lookup.chars_per_message_max))
    effective_around_user = around_user if around_user is not None else config.lookup.around_user_default

    streams = await find_user_streams(person_id=person_id, platform=normalized_platform, chat_scope=normalized_scope, group_id=group_id, max_streams=normalized_max_streams, config=config)

    stream_payloads: list[dict[str, Any]] = []
    for stream in streams:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid:
            continue
        messages = await get_messages_for_stream(stream_id=sid, person_id=person_id, limit=normalized_limit, around_user=effective_around_user)
        message_items = [message_to_dict(message, max_chars=normalized_chars, current_person_id=person_id, config=config) for message in messages]
        payload = {"stream": stream_to_dict(stream, config), "message_count": len(message_items), "messages": message_items}
        if config.lookup.include_timeline_text:
            payload["timeline"] = build_timeline(stream, message_items, config)
        stream_payloads.append(payload)

    return True, {
        "action": "send_to_lookup_user_context",
        "ok": True,
        "target": {"platform": normalized_platform, "user_id": resolved_user_id, "person_id": redact_person_id(person_id, config), "person_info": person_info},
        "params": {"chat_scope": normalized_scope, "group_id": normalize_text(group_id), "per_stream_limit": normalized_limit, "max_streams": normalized_max_streams, "around_user": effective_around_user},
        "total_streams": len(stream_payloads),
        "streams": stream_payloads,
        "elapsed_seconds": round(time.time() - started_at, 3),
        "hint": "sender_role=target_user 表示目标用户，bot 表示机器人自身，other 表示上下文中的其他人。",
    }
