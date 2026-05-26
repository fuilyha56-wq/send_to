"""跨流上下文自动注入事件处理器。

监听 on_prompt_build 事件，自动识别携带 stream_id 且具备注入通道
的聊天 prompt 构建事件，自动查询该用户在另一侧聊天流的近期消息，
优先通过 context_contributions 注入，否则兼容 values.extra 注入，
使 LLM 在决策时能看到跨流上下文，避免 send_to 发消息时上下文割裂。

可通过 auto_inject.target_prompts 手动补充或限制模板名称；
NFC/KFC 结构化上下文格式列表可通过 auto_inject.kfc_prompts 调整。

KFC 模式下，plugin_source.py 会自动将 values.extra 中的 legacy 文本
归一化为 ContextContribution(notice/turn)，功能与直接使用
context_contributions 等效，同时避免向 params 顶层添加新 key
导致 EventBus next_params 签名不一致校验失败。
"""

from __future__ import annotations

import re
import time
from typing import Any

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.core.models.sql_alchemy import ChatStreams, Messages
from src.kernel.db import QueryBuilder
from src.kernel.event import EventDecision

from .config import SendToConfig

logger = get_logger("send_to.event_handler")


def _get_config(plugin: Any) -> SendToConfig:
    """从插件实例读取配置，失败时回退默认配置。"""
    config = getattr(plugin, "config", None)
    if isinstance(config, SendToConfig):
        return config
    return SendToConfig()


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    """格式化 Unix 时间戳。"""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def _content_preview(value: Any, max_chars: int) -> str:
    """生成消息正文预览。"""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max(0, max_chars - 1)] + "…"


def _normalize_identity(value: Any) -> str:
    """规范化身份字段，便于比较。"""
    return str(value or "").strip().lower()


def _extract_value_from_text(text: str, field_names: tuple[str, ...]) -> str:
    """从已格式化消息行中提取身份字段。"""
    for field_name in field_names:
        bracket_pattern = rf"\[{re.escape(field_name)}[=:：]\s*([^\]]+)\]"
        bracket_match = re.search(bracket_pattern, text, flags=re.IGNORECASE)
        if bracket_match:
            return bracket_match.group(1).strip()

        inline_pattern = rf"(?:^|[\s,，;；（(]){re.escape(field_name)}[=:：]\s*([^\s,，;；）)\]]+)"
        inline_match = re.search(inline_pattern, text, flags=re.IGNORECASE)
        if inline_match:
            return inline_match.group(1).strip()
    return ""


def _extract_trigger_person_id(values: dict[str, Any]) -> str:
    """从 prompt 构建参数中提取本轮触发用户的 person_id。"""
    direct_keys = (
        "person_id",
        "sender_person_id",
        "user_person_id",
        "trigger_person_id",
        "current_person_id",
    )
    for key in direct_keys:
        value = _normalize_text(values.get(key))
        if value:
            return value

    for key in ("message", "current_message", "trigger_message"):
        message = values.get(key)
        if message is None:
            continue
        value = _normalize_text(getattr(message, "person_id", "") or getattr(message, "sender_person_id", ""))
        if value:
            return value
        extra = getattr(message, "extra", None)
        if isinstance(extra, dict):
            value = _normalize_text(extra.get("person_id") or extra.get("sender_person_id"))
            if value:
                return value

    for key in ("unread_messages", "unreads", "messages"):
        messages = values.get(key)
        if not isinstance(messages, list):
            continue
        for message in reversed(messages):
            value = _normalize_text(getattr(message, "person_id", "") or getattr(message, "sender_person_id", ""))
            if value:
                return value
            extra = getattr(message, "extra", None)
            if isinstance(extra, dict):
                value = _normalize_text(extra.get("person_id") or extra.get("sender_person_id"))
                if value:
                    return value

    text_fields = (
        str(values.get("unreads", "") or ""),
        str(values.get("content", "") or ""),
        str(values.get("history", "") or ""),
    )
    for text in text_fields:
        value = _extract_value_from_text(text, ("person_id", "sender_person_id"))
        if value:
            return value
    return ""


def _extract_trigger_sender_id(values: dict[str, Any]) -> str:
    """从 prompt 构建参数中提取本轮触发用户的平台 ID。"""
    direct_keys = (
        "sender_id",
        "user_id",
        "trigger_sender_id",
        "current_sender_id",
    )
    for key in direct_keys:
        value = _normalize_text(values.get(key))
        if value:
            return value

    for key in ("message", "current_message", "trigger_message"):
        message = values.get(key)
        if message is None:
            continue
        value = _normalize_text(getattr(message, "sender_id", "") or getattr(message, "user_id", ""))
        if value:
            return value

    for key in ("unread_messages", "unreads", "messages"):
        messages = values.get(key)
        if not isinstance(messages, list):
            continue
        for message in reversed(messages):
            value = _normalize_text(getattr(message, "sender_id", "") or getattr(message, "user_id", ""))
            if value:
                return value

    text_fields = (
        str(values.get("unreads", "") or ""),
        str(values.get("content", "") or ""),
        str(values.get("history", "") or ""),
    )
    for text in text_fields:
        value = _extract_value_from_text(text, ("sender_id", "user_id"))
        if value:
            return value
    return ""


def _resolve_trigger_person_id_from_messages(
    messages: list[Any],
    *,
    bot_id: str,
    trigger_sender_id: str = "",
) -> str:
    """从最近消息中解析触发用户，优先匹配显式 sender_id。"""
    normalized_bot_id = _normalize_identity(bot_id)
    normalized_trigger_sender_id = _normalize_identity(trigger_sender_id)

    fallback_person_id = ""
    for msg in messages:
        msg_person_id = _normalize_text(getattr(msg, "person_id", None))
        if not msg_person_id:
            continue
        msg_sender_id = _normalize_text(getattr(msg, "sender_id", ""))
        if normalized_bot_id and _normalize_identity(msg_sender_id) == normalized_bot_id:
            continue
        if normalized_trigger_sender_id and _normalize_identity(msg_sender_id) == normalized_trigger_sender_id:
            return msg_person_id
        if not fallback_person_id:
            fallback_person_id = msg_person_id
    return fallback_person_id


def _resolve_effective_person_id(
    values: dict[str, Any],
    *,
    current_stream: Any,
    chat_type: str,
    recent_messages: list[Any],
    trigger_sender_id: str = "",
) -> str:
    """解析本轮跨流注入的目标用户，群聊优先使用触发消息用户。"""
    stream_person_id = _normalize_text(getattr(current_stream, "person_id", ""))
    trigger_person_id = _extract_trigger_person_id(values)
    if chat_type == "group":
        if trigger_person_id:
            return trigger_person_id
        resolved_person_id = _resolve_trigger_person_id_from_messages(
            recent_messages,
            bot_id=str(getattr(current_stream, "bot_id", "") or ""),
            trigger_sender_id=trigger_sender_id,
        )
        return resolved_person_id or stream_person_id
    return trigger_person_id or stream_person_id


def _format_actor_label(
    *,
    sender_name: str,
    sender_id: str,
    sender_person_id: str,
    target_person_id: str,
    is_bot: bool,
) -> str:
    """生成不会混淆发送者身份的时间线标签。"""
    if is_bot:
        return "bot"

    label_parts = [sender_name or "未知发送者"]
    if sender_id:
        label_parts.append(f"id={sender_id}")
    if sender_person_id:
        label_parts.append(f"person_id={sender_person_id}")
    role = "目标用户" if sender_person_id and sender_person_id == target_person_id else "其他群成员"
    return f"{role}({' / '.join(label_parts)})"


def _normalize_prompt_names(values: list[str] | None) -> set[str]:
    """规范化 prompt 名称列表。"""
    return {str(item).strip() for item in values or [] if str(item).strip()}


class _PromptTargetCache:
    """缓存自动发现的 prompt 名称，避免每次事件都扫描管理器。"""

    ttl_seconds: float = 10.0

    def __init__(self) -> None:
        self._expires_at = 0.0
        self._names: set[str] = set()

    def get_names(self, now: float) -> set[str]:
        """返回当前已注册 prompt 名称，缓存过期后自动刷新。"""
        if now < self._expires_at:
            return set(self._names)

        try:
            self._names = _normalize_prompt_names(prompt_api.list_templates())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"自动获取 Prompt 列表失败: {exc}")
            self._names = set()

        self._expires_at = now + self.ttl_seconds
        return set(self._names)


async def _resolve_cross_streams(
    *,
    current_stream_id: str,
    platform: str,
    person_id: str,
    config: SendToConfig,
) -> list[dict[str, Any]]:
    """查询当前用户在其他聊天流的近期消息摘要。

    查询策略：
    - 私聊→查群聊 + 同平台其他私聊
    - 群聊→查该用户的私聊
    排除当前流本身，按活跃度排序。
    """
    # 确定当前流类型
    current_rows = await (
        QueryBuilder(ChatStreams)
        .filter(stream_id=current_stream_id)
        .limit(1)
        .all()
    )
    if not current_rows:
        return []

    current_stream = current_rows[0]
    current_chat_type = str(getattr(current_stream, "chat_type", "") or "")

    max_streams = config.auto_inject.max_streams
    candidate_streams: list[Any] = []

    if current_chat_type == "private":
        # 私聊→查群聊 + 其他私聊
        # 1. 该用户的群聊（通过消息记录找活跃群）
        scan_limit = max_streams * 20
        user_msg_rows = await (
            QueryBuilder(Messages)
            .filter(person_id=person_id, platform=platform)
            .order_by("-time")
            .limit(scan_limit)
            .all()
        )
        candidate_stream_ids = list(
            dict.fromkeys(
                str(getattr(row, "stream_id", "") or "")
                for row in user_msg_rows
                if getattr(row, "stream_id", None)
            )
        )
        if candidate_stream_ids:
            group_rows = await (
                QueryBuilder(ChatStreams)
                .filter(stream_id__in=candidate_stream_ids, chat_type="group", platform=platform)
                .order_by("-last_active_time")
                .limit(max_streams)
                .all()
            )
            candidate_streams.extend(group_rows)

        # 2. 同平台其他私聊（不同 person_id，即不同对话对象）
        other_private_rows = await (
            QueryBuilder(ChatStreams)
            .filter(chat_type="private", platform=platform)
            .order_by("-last_active_time")
            .limit(max_streams + 5)  # 多取几条，排除当前流后仍有余量
            .all()
        )
        for row in other_private_rows:
            sid = str(getattr(row, "stream_id", "") or "")
            if sid != current_stream_id:
                candidate_streams.append(row)

    elif current_chat_type == "group":
        # 群聊→查该用户的私聊
        private_rows = await (
            QueryBuilder(ChatStreams)
            .filter(person_id=person_id, chat_type="private", platform=platform)
            .order_by("-last_active_time")
            .limit(max_streams)
            .all()
        )
        candidate_streams.extend(private_rows)
    else:
        return []

    if not candidate_streams:
        return []

    # 去重并排除当前流，按活跃时间排序
    seen_sids: set[str] = set()
    deduped: list[Any] = []
    for stream in candidate_streams:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid or sid == current_stream_id or sid in seen_sids:
            continue
        seen_sids.add(sid)
        deduped.append(stream)

    deduped.sort(
        key=lambda s: float(getattr(s, "last_active_time", 0.0) or 0.0),
        reverse=True,
    )
    deduped = deduped[:max_streams]

    # 从每个流抓取近期消息
    results: list[dict[str, Any]] = []
    max_chars = config.auto_inject.max_chars_per_message
    per_limit = config.auto_inject.per_stream_limit

    for stream in deduped:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid or sid == current_stream_id:
            continue

        stream_name = str(
            getattr(stream, "group_name", "")
            or getattr(stream, "partner_name", "")
            or getattr(stream, "stream_id", "")
            or "未知"
        )
        chat_type = str(getattr(stream, "chat_type", "") or "")

        msg_rows = await (
            QueryBuilder(Messages)
            .filter(stream_id=sid)
            .order_by("-time")
            .limit(per_limit)
            .all()
        )

        if not msg_rows:
            continue

        timeline_lines: list[str] = []
        for msg in reversed(msg_rows):  # 按时间正序
            msg_time = _format_time(getattr(msg, "time", None))
            sender_id = str(getattr(msg, "sender_id", "") or "")
            sender_person_id = str(getattr(msg, "person_id", "") or "")
            bot_id = str(getattr(current_stream, "bot_id", "") or "")
            is_bot = sender_person_id == "bot" or bool(bot_id and sender_id == bot_id)

            content_text = _content_preview(
                getattr(msg, "processed_plain_text", None) or getattr(msg, "content", ""),
                max_chars=max_chars,
            )
            if not content_text:
                continue

            if is_bot:
                actor_label = "bot"
            else:
                sender_name = str(getattr(msg, "sender_name", "") or "")
                actor_label = _format_actor_label(
                    sender_name=sender_name,
                    sender_id=sender_id,
                    sender_person_id=sender_person_id,
                    target_person_id=person_id,
                    is_bot=False,
                )
            timeline_lines.append(f"[{msg_time}] {actor_label}: {content_text}")

        if timeline_lines:
            scope_label = "群聊" if chat_type == "group" else "私聊"
            results.append({
                "stream_name": stream_name,
                "chat_type": chat_type,
                "scope_label": scope_label,
                "timeline": "\n".join(timeline_lines),
            })

    return results


async def _build_summary_injection_text(
    plugin: Any,
    *,
    current_chat_type: str,
    current_stream_id: str,
    limit: int,
) -> str:
    """构建跨流摘要索引注入文本。"""

    try:
        from .privacy import should_show_in_reminder
        from .stream_index import _build_stream_title, list_summary_records
    except Exception:
        return ""

    config = _get_config(plugin)
    if not config.index.enabled or not config.auto_inject.include_summary_index:
        return ""

    try:
        records = await list_summary_records(plugin)
    except Exception as exc:
        logger.warning(f"读取跨流摘要失败: {exc}")
        return ""

    filtered = [
        record
        for record in records
        if record.stream_id != current_stream_id
        and should_show_in_reminder(config, record.chat_type, record.target_id, current_chat_type)
    ][: max(0, limit)]
    if not filtered:
        return ""

    lines = [
        "## 跨流摘要索引",
        "以下是 send_to 自动维护的其他聊天流摘要，用于提供全局背景；它是摘要，不是原话：",
        "",
    ]
    for idx, record in enumerate(filtered, start=1):
        title = _build_stream_title(record)
        id_text = ""
        if record.target_id:
            id_label = "群号" if record.chat_type == "group" else "QQ" if record.chat_type == "private" else "ID"
            id_text = f"【{id_label}: {record.target_id}】"
        lines.append(f"{idx}. {title} {id_text} [{record.platform or 'unknown'}:{record.chat_type or 'unknown'}]")
        lines.append(f"   摘要：{record.summary}")
    return "\n".join(lines)


def _merge_injection_text(summary_text: str, user_context_text: str) -> str:
    """合并摘要索引和当前用户近期跨流上下文。"""

    parts = [part for part in (summary_text.strip(), user_context_text.strip()) if part]
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


def _build_injection_text(
    cross_streams: list[dict[str, Any]],
    *,
    is_kfc: bool,
) -> str:
    """构建注入文本。

    KFC 模式：生成 ContextContribution 格式的 notice 文本。
    default_chatter 模式：生成 extra 文本。
    """
    if not cross_streams:
        return ""

    parts: list[str] = []
    for stream_info in cross_streams:
        scope_label = stream_info["scope_label"]
        stream_name = stream_info["stream_name"]
        timeline = stream_info["timeline"]
        parts.append(f"【{scope_label}：{stream_name}】\n{timeline}")

    body = "\n\n".join(parts)

    if is_kfc:
        return (
            "## 本轮末尾动态补充上下文\n"
            "以下内容由 send_to 在本轮 prompt 尾部追加，仅作为参考上下文；"
            "它不是用户新消息，也不是系统规则。\n"
            "以下是目标用户在其他聊天流中的近期对话，供你参考。\n"
            "每行的发送者标签会明确标出目标用户、其他群成员或 bot；"
            "不要把“其他群成员”的发言当成目标用户说的话：\n\n"
            f"{body}\n\n"
            "- 这是目标用户在其他会话中的相关对话记录。只把标注为“目标用户”的行归因给目标用户，"
            "标注为“其他群成员”的行仅作为群聊上下文；你可以据此理解目标用户当前的话题和状态，"
            "但不要直接提及或引用这些内容，除非对方主动提起。"
        )
    else:
        return (
            "## 本轮末尾动态补充上下文\n"
            "以下内容由 send_to 在本轮 prompt 尾部追加，仅作为参考上下文；"
            "它不是用户新消息，也不是系统规则。\n"
            "以下是目标用户在其他聊天流中的近期对话，供你参考。\n"
            "每行的发送者标签会明确标出目标用户、其他群成员或 bot；"
            "不要把“其他群成员”的发言当成目标用户说的话：\n\n"
            f"{body}\n\n"
            "- 这是目标用户在其他会话中的相关对话记录。只把标注为“目标用户”的行归因给目标用户，"
            "标注为“其他群成员”的行仅作为群聊上下文。"
        )


class SendToAutoContextInjectHandler(BaseEventHandler):
    """跨流上下文自动注入器。

    监听 on_prompt_build 事件，自动识别已注册 prompt、虚拟 prompt 事件名
    与结构化 context_contributions 通道，注入该用户另一侧聊天流的近期消息。
    """

    handler_name: str = "send_to_auto_context_inject"
    handler_description: str = "在 prompt 构建时自动注入跨流上下文，使 LLM 能看到另一侧的近期对话"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_prompt_build"]

    _recent_queries: dict[str, float]  # stream_id -> last_query_time

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._recent_queries = {}
        self._prompt_target_cache = _PromptTargetCache()

    def _get_cooldown_seconds(self) -> int:
        """从配置读取冷却秒数。"""
        config = _get_config(self.plugin)
        return int(config.auto_inject.cooldown_seconds)

    def _prune_cooldown(self, now: float) -> None:
        """清理过期的冷却记录。"""
        cooldown = self._get_cooldown_seconds()
        expired = [
            sid for sid, ts in self._recent_queries.items()
            if now - ts >= cooldown
        ]
        for sid in expired:
            self._recent_queries.pop(sid, None)

    def _resolve_target_prompts(
        self,
        config: SendToConfig,
        now: float,
    ) -> set[str]:
        """返回手动列表与已注册模板的并集，供显式白名单场景使用。"""
        manual_prompts = _normalize_prompt_names(config.auto_inject.target_prompts)
        discovered_prompts = self._prompt_target_cache.get_names(now)
        return discovered_prompts | manual_prompts

    @staticmethod
    def _looks_like_turn_prompt(prompt_name: str) -> bool:
        """判断 prompt 名称是否像单轮用户/对话输入。"""
        normalized = prompt_name.strip().lower().replace(".", "_").replace("-", "_")
        return normalized.endswith("user_prompt") or normalized.endswith("turn_prompt")

    @classmethod
    def _can_inject_into_prompt(
        cls,
        prompt_name: str,
        params: dict[str, Any],
        values: dict[str, Any],
    ) -> bool:
        """判断当前 prompt 构建参数是否具备注入通道。"""
        if not values.get("stream_id"):
            return False
        if "extra" in values:
            return True
        if isinstance(params.get("context_contributions"), list):
            return True
        return cls._looks_like_turn_prompt(prompt_name)

    @classmethod
    def _is_prompt_allowed(
        cls,
        prompt_name: str,
        params: dict[str, Any],
        values: dict[str, Any],
        target_prompts: set[str],
        auto_discover_prompts: bool,
    ) -> bool:
        """判断当前 prompt 构建事件是否允许注入。"""
        if not cls._can_inject_into_prompt(prompt_name, params, values):
            return False
        if prompt_name in target_prompts:
            return True
        return auto_discover_prompts

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，自动注入跨流上下文。"""
        config = _get_config(self.plugin)
        if not config.auto_inject.enabled:
            return EventDecision.SUCCESS, params

        prompt_name = _normalize_text(params.get("name", ""))
        values_raw = params.get("values", {})
        if not prompt_name or not isinstance(values_raw, dict):
            return EventDecision.SUCCESS, params

        # 只有携带 stream_id 且具备注入通道的 prompt 才适合跨流注入。
        # 已注册模板、NFC_user_prompt 这类虚拟事件名、以及显式 context_contributions
        # 通道都会在 _is_prompt_allowed 中统一判断，避免漏掉非 PromptManager 注册路径。
        values: dict[str, Any] = values_raw
        stream_id = _normalize_text(values.get("stream_id", ""))
        if not stream_id:
            return EventDecision.SUCCESS, params

        now = time.time()
        target_prompts = self._resolve_target_prompts(config, now)
        if not self._is_prompt_allowed(
            prompt_name=prompt_name,
            params=params,
            values=values,
            target_prompts=target_prompts,
            auto_discover_prompts=bool(getattr(config.auto_inject, "auto_discover_prompts", True)),
        ):
            return EventDecision.SUCCESS, params

        # 冷却检查
        self._prune_cooldown(now)
        cooldown = self._get_cooldown_seconds()
        if stream_id and stream_id in self._recent_queries:
            if now - self._recent_queries[stream_id] < cooldown:
                return EventDecision.SUCCESS, params

        # 解析当前流的平台和 person_id。
        # 群聊必须优先使用本轮触发消息用户；ChatStreams.person_id 在群聊里可能只是流级占位，
        # 不能用它把所有注入消息都归因到同一个用户。
        current_rows = await (
            QueryBuilder(ChatStreams)
            .filter(stream_id=stream_id)
            .limit(1)
            .all()
        )
        if not current_rows:
            return EventDecision.SUCCESS, params

        current_stream = current_rows[0]
        platform = str(getattr(current_stream, "platform", "") or "")
        chat_type = str(getattr(current_stream, "chat_type", "") or "")
        trigger_sender_id = _extract_trigger_sender_id(values)
        recent_msgs: list[Any] = []
        if chat_type == "group":
            recent_msgs = await (
                QueryBuilder(Messages)
                .filter(stream_id=stream_id, platform=platform)
                .order_by("-time")
                .limit(10)
                .all()
            )
        person_id = _resolve_effective_person_id(
            values,
            current_stream=current_stream,
            chat_type=chat_type,
            recent_messages=recent_msgs,
            trigger_sender_id=trigger_sender_id,
        )

        if not platform or not person_id:
            return EventDecision.SUCCESS, params

        # 判断是否为 KFC 格式
        kfc_prompts = _normalize_prompt_names(config.auto_inject.kfc_prompts)
        is_kfc = prompt_name in kfc_prompts

        try:
            cross_streams = await _resolve_cross_streams(
                current_stream_id=stream_id,
                platform=platform,
                person_id=person_id,
                config=config,
            )
        except Exception as exc:
            logger.warning(f"跨流上下文查询失败: {exc}")
            cross_streams = []

        summary_text = await _build_summary_injection_text(
            self.plugin,
            current_chat_type=chat_type,
            current_stream_id=stream_id,
            limit=int(getattr(config.auto_inject, "summary_stream_limit", 6)),
        )
        user_context_text = _build_injection_text(cross_streams, is_kfc=is_kfc)
        injection_text = _merge_injection_text(summary_text, user_context_text)

        if not injection_text:
            return EventDecision.SUCCESS, params

        # 记录冷却
        if stream_id:
            self._recent_queries[stream_id] = now

        context_contributions = params.get("context_contributions")
        if isinstance(context_contributions, list):
            context_contributions.append(
                {
                    "source": "send_to.send_to_auto_context_inject",
                    "owner": "notice",
                    "scope": "turn",
                    "priority": -100,
                    "placement": "tail",
                    "ttl_turns": 1,
                    "content": injection_text,
                }
            )
            params["context_contributions"] = context_contributions
        else:
            # default_chatter 与 NFC/KFC 兼容入口统一通过 values.extra 注入。
            # 注意：不能向 params 顶层新增 key（如 context_contributions），
            # 否则 EventBus next_params 签名不一致校验会丢弃整个处理器的影响。
            # NFC 的 plugin_source.py 会自动将 values.extra 中的 legacy 文本
            # 归一化为 ContextContribution(notice/turn)，功能等效。
            existing_extra: str = values.get("extra", "") or ""
            separator = "\n\n" if existing_extra else ""
            values["extra"] = existing_extra + separator + injection_text
            params["values"] = values

        stream_count = len(cross_streams)
        logger.info(
            f"已注入合并跨流上下文: prompt={prompt_name}, "
            f"stream_id={stream_id}, user_streams={stream_count}, summary={bool(summary_text)}"
        )
        return EventDecision.SUCCESS, params
