"""send_to Action：让 LLM 主动向其他聊天流发送消息或执行动作。

设计思路：
- 目标可用 platform + user_id 精确定位，也可用 hint（昵称/群名片）解析
- 支持两种目标类型：group（群聊）和 private（私聊）
- 支持两种操作类型：send_text（发文本）和 execute_action（在目标流执行动作）
- LLM 自行判断是否调用与如何填参数
"""

from __future__ import annotations

from typing import Annotated, Any, AsyncGenerator, cast

from src.app.plugin_system.api.action_api import execute_action
from src.app.plugin_system.base import BaseAction
from src.app.plugin_system.types import Message, MessageType
from src.core.models.sql_alchemy import ChatStreams, PersonInfo
from src.core.models.stream import ChatStream
from src.core.utils.user_query_helper import get_user_query_helper
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

logger = get_logger("send_to")


async def _get_stream_info(stream_id: str) -> dict[str, Any] | None:
    from src.core.managers.stream_manager import get_stream_manager

    stream_manager = get_stream_manager()
    stream_info = await stream_manager.get_stream_info(stream_id)
    if isinstance(stream_info, dict):
        return stream_info
    return None


async def _resolve_group_id(platform: str, hint: str) -> tuple[str | None, str]:
    normalized = str(hint or "").strip()
    if not normalized:
        return None, "group_hint 为空"

    if normalized.isdigit():
        return normalized, ""

    rows = await (
        QueryBuilder(ChatStreams)
        .filter(platform=platform, chat_type="group")
        .all()
    )
    streams = cast(list[ChatStreams], rows)

    normalized_lower = normalized.lower()
    exact: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []

    for stream in streams:
        gid = str(getattr(stream, "group_id", "") or "").strip()
        gname = str(getattr(stream, "group_name", "") or "").strip()
        if not gid:
            continue

        if gname and gname.lower() == normalized_lower:
            exact.append((gid, gname))
            continue
        if gname and normalized_lower in gname.lower():
            partial.append((gid, gname))

    # 去重：同一 gid 保留首次出现的 gname，避免 dict 去重丢失信息
    seen_gids: set[str] = set()
    unique_exact: list[tuple[str, str]] = []
    for gid, gname in exact:
        if gid not in seen_gids:
            seen_gids.add(gid)
            unique_exact.append((gid, gname))
    if len(unique_exact) == 1:
        return unique_exact[0][0], ""
    if len(unique_exact) > 1:
        names = "、".join(f"{n}({g})" for g, n in unique_exact[:5])
        return None, f"group_hint='{hint}' 精确命中多个群：{names}，请用 group_id 指定"

    seen_gids.clear()
    unique_partial: list[tuple[str, str]] = []
    for gid, gname in partial:
        if gid not in seen_gids:
            seen_gids.add(gid)
            unique_partial.append((gid, gname))
    if len(unique_partial) == 1:
        return unique_partial[0][0], ""
    if len(unique_partial) > 1:
        names = "、".join(f"{n}({g})" for g, n in unique_partial[:5])
        return None, f"group_hint='{hint}' 模糊命中多个群：{names}，请用 group_id 指定"

    return None, f"group_hint='{hint}' 未匹配到任何群"


async def _resolve_user_id(platform: str, hint: str) -> tuple[str | None, str]:
    helper = get_user_query_helper()
    normalized_hint = str(hint or "").strip()

    # 纯数字视为直接 user_id（QQ 号等），与 _resolve_group_id 行为一致
    if normalized_hint.isdigit():
        return normalized_hint, ""

    resolved = await helper.resolve_user_id(platform, normalized_hint)
    if resolved:
        return resolved, ""

    candidates = await _lookup_user_candidates(platform, normalized_hint, limit=5)
    if candidates:
        names = "、".join(
            f"{item['display_name']}({item['user_id']})" for item in candidates
        )
        return None, (
            f"无法通过 user_hint='{normalized_hint}' 唯一定位用户，候选：{names}。"
            "请提供更精确的昵称或直接给出 user_id。"
        )

    return None, (
        f"无法通过 user_hint='{normalized_hint}' 唯一定位用户。"
        "请提供更精确的昵称或直接给出 user_id。"
    )


async def _lookup_user_candidates(
    platform: str,
    keyword: str,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_keyword = str(keyword or "").strip().lstrip("@").strip()
    if not normalized_keyword:
        return []

    rows = await (
        QueryBuilder(PersonInfo).filter(platform=platform).all()
        if platform
        else QueryBuilder(PersonInfo).all()
    )
    persons = cast(list[PersonInfo], rows)
    keyword_lower = normalized_keyword.lower()
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []

    for person in persons:
        uid = str(getattr(person, "user_id", "") or "").strip()
        if not uid:
            continue

        nickname = str(getattr(person, "nickname", "") or "").strip()
        cardname = str(getattr(person, "cardname", "") or "").strip()
        display_name = cardname or nickname or uid
        record = {
            "user_id": uid,
            "nickname": nickname or None,
            "cardname": cardname or None,
            "display_name": display_name,
            "platform": str(getattr(person, "platform", "") or ""),
            "interaction_count": getattr(person, "interaction_count", None),
        }

        uid_lower = uid.lower()
        nickname_lower = nickname.lower()
        cardname_lower = cardname.lower()

        # 精确匹配：user_id / 昵称 / 群名片
        if uid_lower == keyword_lower or nickname_lower == keyword_lower or cardname_lower == keyword_lower:
            exact.append(record)
            continue
        # 模糊匹配：user_id / 昵称 / 群名片
        if keyword_lower in uid_lower or keyword_lower in nickname_lower or keyword_lower in cardname_lower:
            partial.append(record)

        if len(exact) + len(partial) >= limit * 3:
            break

    return (exact + partial)[:limit]


async def _resolve_stream_id(
    target_type: str,
    effective_platform: str,
    group_id: str,
    group_hint: str,
    user_id: str,
    user_hint: str,
    current_stream_id: str,
) -> tuple[str | None, str, str]:
    if target_type == "group":
        normalized_group_id = str(group_id or "").strip()
        normalized_group_hint = str(group_hint or "").strip()

        if not normalized_group_id and not normalized_group_hint:
            return None, "发送到群聊必须提供 group_id 或 group_hint", ""

        if not normalized_group_id:
            resolved_gid, reason = await _resolve_group_id(
                effective_platform, normalized_group_hint
            )
            if not resolved_gid:
                return None, (
                    f"{reason}。可以调用 send_to_list_groups 查看可用群列表后再决定。"
                ), ""
            normalized_group_id = resolved_gid

        target_stream_id = ChatStream.generate_stream_id(
            effective_platform, group_id=normalized_group_id
        )
        if target_stream_id == current_stream_id:
            return None, "目标就是当前会话，请直接回复而非使用 send_to", ""
        return target_stream_id, "", normalized_group_id

    normalized_user_id = str(user_id or "").strip()
    normalized_hint = str(user_hint or "").strip()

    if not normalized_user_id and not normalized_hint:
        return None, "发送到私聊必须提供 user_id 或 user_hint", ""

    if not normalized_user_id:
        resolved_uid, reason = await _resolve_user_id(effective_platform, normalized_hint)
        if not resolved_uid:
            return None, reason, ""
        normalized_user_id = resolved_uid

    target_stream_id = ChatStream.generate_stream_id(
        effective_platform, user_id=normalized_user_id
    )
    if target_stream_id == current_stream_id:
        return None, "目标就是当前会话，请直接回复而非使用 send_to", ""
    return target_stream_id, "", normalized_user_id


class SendToAction(BaseAction):
    """跨聊天流发送文本消息的 Action。"""

    name: str = "send_to"
    description: str = (
        "向**其他**聊天流（非当前会话）发送一条文本消息。"
        "使用场景：当前在 A 聊天，需要主动把消息转告或发送给 B 聊天（群或某人私聊）。"
        "如果只是回复当前会话，请不要使用这个动作。"
        "target_type='group' 时提供 group_id 或 group_hint（群名）；"
        "target_type='private' 时提供 user_id 或 user_hint（昵称/群名片）。"
        "hint 类参数会做精确/模糊匹配，多命中时会返回歧义提示，请向用户确认后换用 ID 或更精确的名称。"
    )
    associated_types: list[str] = ["text"]
    primary_action: bool = False

    async def execute(
        self,
        target_type: Annotated[
            str,
            "目标类型：'group' 表示发送到群聊，'private' 表示发送到某人的私聊",
        ],
        content: Annotated[str, "要发送的文本内容"],
        group_id: Annotated[
            str,
            "目标群的原始 ID（当 target_type='group' 时二选一，优先级高于 group_hint）",
        ] = "",
        group_hint: Annotated[
            str,
            "目标群的群名（当 target_type='group' 且不知道 group_id 时使用，会按群名精确/模糊唯一匹配；多命中时会返回歧义提示）",
        ] = "",
        user_id: Annotated[
            str,
            "目标用户的平台原始 ID（当 target_type='private' 时必填，如果只知道昵称可用 user_hint 代替）",
        ] = "",
        user_hint: Annotated[
            str,
            "目标用户的昵称或群名片（当 target_type='private' 且不知道 user_id 时使用，会尝试唯一解析）",
        ] = "",
        platform: Annotated[
            str,
            "目标平台标识（如 qq、wechat）。默认与当前会话相同，通常不需要填写。",
        ] = "",
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        text = str(content or "").strip()
        if not text:
            yield False, "content 不能为空"
            return

        normalized_type = str(target_type or "").strip().lower()
        if normalized_type not in ("group", "private"):
            yield False, "target_type 必须是 'group' 或 'private'"
            return

        effective_platform = str(platform or "").strip() or self.chat_stream.platform
        if not effective_platform:
            yield False, "无法确定目标平台"
            return

        target_stream_id, err, resolved_id = await _resolve_stream_id(
            normalized_type,
            effective_platform,
            group_id,
            group_hint,
            user_id,
            user_hint,
            self.chat_stream.stream_id,
        )
        if err:
            yield False, err
            return

        target_desc = f"{'群' if normalized_type == 'group' else '用户'} {target_stream_id[:20] if target_stream_id else '?'}"

        yield None

        # 构造消息并直接通过 MessageSender 发送（而非 send_text），
        # 以便发送后将消息注入目标流的 unread_messages 并启动 stream_loop，
        # 避免消息仅写入 history_messages 导致目标 bot 不响应。
        from uuid import uuid4

        from src.core.managers.stream_manager import get_stream_manager
        from src.core.transport.distribution.stream_loop_manager import (
            get_stream_loop_manager,
        )
        from src.core.transport.message_send import get_message_sender

        message_id = f"send_to_{uuid4().hex}"
        message = Message(
            message_id=message_id,
            content=text,
            processed_plain_text=text,
            message_type=MessageType.TEXT,
            platform=effective_platform,
            stream_id=target_stream_id,
            chat_type=normalized_type,
        )

        sender = get_message_sender()
        ok = await sender.send_message(message)

        if ok:
            # 确保目标流在内存中（send_message 内部的 _persist_sent_message_to_history
            # 已将消息写入 history_messages；此处将其移至 unread_messages 并启动流循环）
            sm = get_stream_manager()
            if normalized_type == "group":
                target_stream = await sm.get_or_create_stream(
                    stream_id=target_stream_id,
                    platform=effective_platform,
                    group_id=resolved_id,
                    chat_type="group",
                )
            else:
                target_stream = await sm.get_or_create_stream(
                    stream_id=target_stream_id,
                    platform=effective_platform,
                    user_id=resolved_id,
                    chat_type="private",
                )

            ctx = target_stream.context
            # 从 history 中移除（由 _persist_sent_message_to_history 写入），
            # 改为注入 unread，使目标流 bot 能感知并响应
            ctx.history_messages = [
                m for m in ctx.history_messages if m.message_id != message_id
            ]
            ctx.add_unread_message(message)
            await get_stream_loop_manager().start_stream_loop(target_stream_id)

            logger.info(f"send_to 成功: -> {target_desc} | {text[:60]!r}")
            yield True, f"已向{target_desc}发送消息"
            return

        logger.warning(f"send_to 失败: -> {target_desc} | {text[:60]!r}")
        yield False, f"向{target_desc}发送失败"


async def _build_target_message(
    stream_id: str,
    platform: str,
    chat_type: str,
    trigger_sender_id: str,
    trigger_sender_name: str,
    extra: dict[str, Any] | None = None,
) -> Message:
    from uuid import uuid4

    from src.core.managers.adapter_manager import get_adapter_manager

    adapter_manager = get_adapter_manager()
    bot_info = await adapter_manager.get_bot_info_by_platform(platform)

    return Message(
        message_id=f"send_to_exec_{uuid4().hex}",
        content="[跨流触发动作]",
        processed_plain_text="",
        message_type=MessageType.TEXT,
        sender_id=trigger_sender_id or (bot_info.get("bot_id", "") if bot_info else ""),
        sender_name=trigger_sender_name or (bot_info.get("bot_name", "Bot") if bot_info else "Bot"),
        platform=platform,
        chat_type=chat_type,
        stream_id=stream_id,
        **(extra or {}),
    )


class SendToExecuteAction(BaseAction):
    """跨聊天流执行 Action：在目标聊天流中执行指定动作（如画图、查询等）。"""

    name: str = "send_to_execute"
    description: str = (
        "在**其他**聊天流中执行一个指定的 Action（动作）。"
        "与 send_to（仅发文本）不同，此动作用于触发另一个流的某个能力，"
        "例如在私聊里触发画图、查天气、执行命令等。"
        "调用时需要指定："
        "1. 目标定位：target_type + group_id/group_hint/user_id/user_hint"
        "2. 目标动作：signature（格式 'plugin_name:action:action_name'）"
        "3. 动作参数：params（JSON 对象，包含要传给目标 Action 的所有参数）"
        "params 应包含目标 Action 所需的全部参数。"
        "例如要触发 nai_artist:action:draw，signature='nai_artist:action:draw'，"
        "params={\"prompt\": \"猫娘\", \"style\": \"anime\"}"
    )
    associated_types: list[str] = ["text"]
    primary_action: bool = False

    async def execute(
        self,
        target_type: Annotated[
            str,
            "目标类型：'group' 表示目标在群聊，'private' 表示目标在私聊",
        ],
        signature: Annotated[
            str,
            "要在目标流执行的 Action 组件签名。"
            "格式 'plugin_name:action:action_name'（插件 Action）。"
            "例如 'nai_artist:action:draw'",
        ],
        params: Annotated[
            str,
            "传递给目标 Action 的参数，JSON 对象格式的字符串。"
            "包含目标 Action 所需的全部参数键值对。"
            "例如：'{\"prompt\": \"猫娘\", \"style\": \"anime\"}'",
        ],
        group_id: Annotated[
            str,
            "目标群的原始 ID（当 target_type='group' 时二选一，优先级高于 group_hint）",
        ] = "",
        group_hint: Annotated[
            str,
            "目标群的群名（当 target_type='group' 且不知道 group_id 时使用）",
        ] = "",
        user_id: Annotated[
            str,
            "目标用户的平台原始 ID（当 target_type='private' 时填写）",
        ] = "",
        user_hint: Annotated[
            str,
            "目标用户的昵称或群名片（当 target_type='private' 且不知道 user_id 时使用）",
        ] = "",
        platform: Annotated[
            str,
            "目标平台标识（如 qq、wechat）。默认与当前会话相同。",
        ] = "",
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        normalized_type = str(target_type or "").strip().lower()
        if normalized_type not in ("group", "private"):
            yield False, "target_type 必须是 'group' 或 'private'"
            return

        normalized_sig = str(signature or "").strip()
        if not normalized_sig:
            yield False, "signature 不能为空"
            return
        if ":" not in normalized_sig:
            yield (
                False,
                f"signature 格式错误: '{normalized_sig}'，"
                "应为 'plugin_name:action:action_name'",
            )
            return

        parts = normalized_sig.split(":", 2)
        if len(parts) != 3:
            yield (
                False,
                f"signature 格式错误: '{normalized_sig}'，"
                "应为 'plugin_name:action:action_name'",
            )
            return

        import json

        action_kwargs: dict[str, Any] = {}
        raw_params = str(params or "").strip()
        if raw_params:
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    action_kwargs = parsed
                else:
                    yield False, "params 必须是 JSON 对象（即 {...}）"
                    return
            except json.JSONDecodeError as e:
                yield False, f"params JSON 解析失败: {e}"
                return

        effective_platform = str(platform or "").strip() or self.chat_stream.platform
        if not effective_platform:
            yield False, "无法确定目标平台"
            return

        target_stream_id, err, resolved_id = await _resolve_stream_id(
            normalized_type,
            effective_platform,
            group_id,
            group_hint,
            user_id,
            user_hint,
            self.chat_stream.stream_id,
        )
        if err:
            yield False, err
            return

        # 确保流记录存在（支持向无聊天记录的用户发送）
        try:
            from src.app.plugin_system.api import stream_api
            if normalized_type == "group":
                await stream_api.get_or_create_stream(
                    stream_id=target_stream_id,
                    platform=effective_platform,
                    group_id=resolved_id,
                    chat_type="group",
                )
            else:
                await stream_api.get_or_create_stream(
                    stream_id=target_stream_id,
                    platform=effective_platform,
                    user_id=resolved_id,
                    chat_type="private",
                )
        except Exception as error:
            # 非致命：execute_action 可能仍能工作，但记录原因便于排查下游失败
            logger.debug(
                f"[send_to_execute] get_or_create_stream 失败 "
                f"stream_id={target_stream_id}: {error}"
            )

        extra: dict[str, Any] = {}
        target_info = await _get_stream_info(target_stream_id)
        if normalized_type == "group":
            # resolved_id 来自 _resolve_stream_id，已包含解析后的 group_id
            if resolved_id:
                extra["target_group_id"] = resolved_id
        elif target_info and target_info.get("person_id"):
            helper = get_user_query_helper()
            person = await helper.person_crud.get_by(
                person_id=target_info["person_id"],
            )
            if person and person.user_id:
                extra["target_user_id"] = str(person.user_id)

        from src.app.plugin_system.api.action_api import get_action_class

        if not get_action_class(normalized_sig):
            yield (
                False,
                f"未找到 Action 组件: '{normalized_sig}'。"
                "可以使用 send_to_list_groups / send_to_lookup_users 检查可用群/用户，"
                "然后用 send_to 发文字消息。",
            )
            return

        yield None

        target_plugin_name = normalized_sig.split(":", 1)[0]
        from src.app.plugin_system.api.plugin_api import get_plugin

        target_plugin = get_plugin(target_plugin_name)
        if not target_plugin:
            yield (
                False,
                f"未找到目标 Action 所属插件 '{target_plugin_name}'，"
                "请确认插件已加载",
            )
            return

        chat_type = normalized_type
        trigger_sender_id = self.chat_stream.context.triggering_user_id or ""

        message = await _build_target_message(
            stream_id=target_stream_id,
            platform=effective_platform,
            chat_type=chat_type,
            trigger_sender_id=trigger_sender_id,
            trigger_sender_name="",
            extra=extra,
        )

        try:
            ok, result = await execute_action(
                signature=normalized_sig,
                plugin=target_plugin,
                message=message,
                **action_kwargs,
            )
        except ValueError as e:
            ok, result = False, str(e)
        except Exception as e:
            logger.error(f"send_to_execute 执行异常: {e}", exc_info=True)
            ok, result = False, f"执行异常: {e}"

        target_desc = f"{'群' if normalized_type == 'group' else '用户'} {target_stream_id[:20]}"
        logger.info(
            f"send_to_execute {normalized_sig} -> {target_desc}: "
            f"{'成功' if ok else '失败'} | {str(result)[:80]}"
        )

        yield ok, f"在目标{target_desc}中执行 {normalized_sig}：{'成功' if ok else '失败'}，{result}"



class SendToSummaryUpdateAction(BaseAction):
    """为当前聊天流写入最新摘要并同步跨流 reminder。"""

    name: str = "send_to_update_stream_summary"
    description: str = (
        "为当前聊天流写入最新摘要，并同步到 send_to 的跨聊天流 system reminder。"
        "仅当当前摘要明显失真、遗漏关键事实或需要立刻记录跨流约定时调用。"
        "输入必须是客观、完整、覆盖旧摘要的新摘要。"
    )
    associated_types: list[str] = ["text"]
    chatter_allow: list[str] = []

    async def execute(
        self,
        summary: Annotated[str, "当前聊天流最新纠偏摘要，覆盖旧摘要而不是追加"],
    ) -> tuple[bool, str]:
        from .stream_index import sync_actor_reminder, upsert_summary

        try:
            changed = await upsert_summary(self.plugin, self.chat_stream, summary)
            await sync_actor_reminder(self.plugin)
        except ValueError as error:
            return False, str(error)

        stream_name = self.chat_stream.stream_name or self.chat_stream.stream_id[:8]
        if changed:
            return True, f"已更新聊天流 {stream_name} 的跨流摘要并同步 reminder"
        return True, f"聊天流 {stream_name} 的摘要无变化，已同步 reminder"


class SendToRelayIntentAction(BaseAction):
    """跨聊天流转告：将意图和上下文注入另一个聊天流。"""

    name: str = "send_to_relay_intent"
    description: str = (
        "跨聊天流意识迁移/转告能力。与 send_to 直接发文本不同，"
        "此 Action 会把开场白、来源上下文和内部提示注入目标流，由目标流的 bot 自然续接。"
        "调用前建议先用 send_to_find_stream 获取 target_stream_id / target_user_id / target_group_id。"
    )
    associated_types: list[str] = ["text"]
    chatter_allow: list[str] = []

    async def execute(
        self,
        target_stream_id: Annotated[str, "目标聊天流 ID，通常来自 send_to_find_stream"] = "",
        relay_content: Annotated[str, "在目标流的自然开场白，包含来源说明和话题承接"] = "",
        context_message_count: Annotated[int, "携带几条源流原始消息作为记忆凭证"] = 10,
        opening_hint: Annotated[str, "给目标流自己的内部提示，用户不可见"] = "",
        target_platform: Annotated[str, "目标平台标识，默认 qq"] = "qq",
        target_user_id: Annotated[str, "私聊目标用户 ID"] = "",
        target_group_id: Annotated[str, "群聊目标群号"] = "",
    ) -> tuple[bool, str]:
        from .relay import relay_intent

        return await relay_intent(
            plugin=self.plugin,
            chat_stream=self.chat_stream,
            target_stream_id=target_stream_id,
            relay_content=relay_content,
            context_message_count=context_message_count,
            opening_hint=opening_hint,
            target_platform=target_platform,
            target_user_id=target_user_id,
            target_group_id=target_group_id,
        )
