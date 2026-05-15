"""send_to Action：让 LLM 主动向其他聊天流发送消息。

设计思路（参考 context_bridge_tool 的用户定位方式）：
- 目标可用 platform + user_id 精确定位，也可用 user_hint（昵称/群名片）解析
- 支持两种目标类型：group（群聊）和 private（私聊）
- LLM 自行判断是否调用与如何填参数
"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator, cast

from src.app.plugin_system.api.send_api import send_text
from src.core.components.base.action import BaseAction
from src.core.models.sql_alchemy import ChatStreams
from src.core.models.stream import ChatStream
from src.core.utils.user_query_helper import get_user_query_helper
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

logger = get_logger("send_to")


async def _resolve_group_id(platform: str, hint: str) -> tuple[str | None, str]:
    """按 hint 解析群 ID。

    规则：
    - 纯数字：直接当作 group_id
    - 在同平台的 ChatStreams 里按 group_name 精确匹配（大小写不敏感）
    - 精确失败则尝试包含匹配；仅在唯一命中时返回
    - 多命中视为歧义（返回 None 并附带原因）

    Returns:
        (group_id, error_reason) — 成功时 error_reason 为空串
    """
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

    unique_exact = list({gid: gname for gid, gname in exact}.items())
    if len(unique_exact) == 1:
        return unique_exact[0][0], ""
    if len(unique_exact) > 1:
        names = "、".join(f"{n}({g})" for g, n in unique_exact[:5])
        return None, f"group_hint='{hint}' 精确命中多个群：{names}，请用 group_id 指定"

    unique_partial = list({gid: gname for gid, gname in partial}.items())
    if len(unique_partial) == 1:
        return unique_partial[0][0], ""
    if len(unique_partial) > 1:
        names = "、".join(f"{n}({g})" for g, n in unique_partial[:5])
        return None, f"group_hint='{hint}' 模糊命中多个群：{names}，请用 group_id 指定"

    return None, f"group_hint='{hint}' 未匹配到任何群"


class SendToAction(BaseAction):
    """跨聊天流发送文本消息的 Action。"""

    action_name: str = "send_to"
    action_description: str = (
        "向**其他**聊天流（非当前会话）发送一条文本消息。"
        "使用场景：当前在 A 聊天，需要主动把消息转告或发送给 B 聊天（群或某人私聊）。"
        "如果只是回复当前会话，请不要使用这个动作。"
        "target_type='group' 时提供 group_id 或 group_hint（群名）；"
        "target_type='private' 时提供 user_id 或 user_hint（昵称/群名片）。"
        "hint 类参数会做精确/模糊匹配，多命中时会返回歧义提示，请向用户确认后换用 ID 或更精确的名称。"
    )
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
        """执行跨聊天流发送。"""
        text = str(content or "").strip()
        if not text:
            yield False, "content 不能为空"
            return

        normalized_type = str(target_type or "").strip().lower()
        if normalized_type not in ("group", "private"):
            yield False, "target_type 必须是 'group' 或 'private'"
            return

        # 默认使用当前会话平台
        effective_platform = str(platform or "").strip() or self.chat_stream.platform
        if not effective_platform:
            yield False, "无法确定目标平台"
            return

        # 解析目标 stream_id
        if normalized_type == "group":
            normalized_group_id = str(group_id or "").strip()
            normalized_group_hint = str(group_hint or "").strip()

            if not normalized_group_id and not normalized_group_hint:
                yield False, "发送到群聊必须提供 group_id 或 group_hint"
                return

            # 优先用 group_id；没有则通过 group_hint 解析
            if not normalized_group_id:
                resolved_gid, reason = await _resolve_group_id(
                    effective_platform, normalized_group_hint
                )
                if not resolved_gid:
                    yield False, (
                        f"{reason}。可以调用 send_to_list_groups 查看可用群列表后再决定。"
                    )
                    return
                normalized_group_id = resolved_gid

            target_stream_id = ChatStream.generate_stream_id(
                effective_platform, group_id=normalized_group_id
            )
            target_desc = f"群 {normalized_group_id}"

            # 防止向当前群发送（其实也可以，但这个 action 语义是"其他流"）
            if target_stream_id == self.chat_stream.stream_id:
                yield False, "目标就是当前会话，请直接回复而非使用 send_to"
                return

        else:
            normalized_user_id = str(user_id or "").strip()
            normalized_hint = str(user_hint or "").strip()

            if not normalized_user_id and not normalized_hint:
                yield False, "发送到私聊必须提供 user_id 或 user_hint"
                return

            # 通过 hint 解析 user_id
            if not normalized_user_id:
                helper = get_user_query_helper()
                resolved = await helper.resolve_user_id(
                    effective_platform, normalized_hint
                )
                if not resolved:
                    yield (
                        False,
                        f"无法通过 user_hint='{normalized_hint}' 唯一定位用户。"
                        "请提供更精确的昵称或直接给出 user_id。",
                    )
                    return
                normalized_user_id = resolved

            target_stream_id = ChatStream.generate_stream_id(
                effective_platform, user_id=normalized_user_id
            )
            target_desc = f"用户 {normalized_user_id} 的私聊"

            if target_stream_id == self.chat_stream.stream_id:
                yield False, "目标就是当前会话，请直接回复而非使用 send_to"
                return

        # 交由统一调度器安排发送（异步生成器 yield None 暂停）
        yield None

        ok = await send_text(
            content=text,
            stream_id=target_stream_id,
            platform=effective_platform,
        )

        if ok:
            logger.info(f"send_to 成功: -> {target_desc} | {text[:60]!r}")
            yield True, f"已向{target_desc}发送消息"
            return

        logger.warning(f"send_to 失败: -> {target_desc} | {text[:60]!r}")
        yield False, f"向{target_desc}发送失败"
