"""send_to 查询工具。"""

from __future__ import annotations

from typing import Annotated, Any, cast

from src.app.plugin_system.base import BaseTool
from src.core.models.sql_alchemy import ChatStreams, PersonInfo
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

from .action import _lookup_user_candidates
from .config import SendToConfig
from .utils import clamp_int

logger = get_logger("send_to.tools")


def _clamp(value: int, *, lo: int, hi: int) -> int:
    """将数值压入范围。"""

    return clamp_int(value, minimum=lo, maximum=hi)


class ListGroupsTool(BaseTool):
    """列出 bot 可见的群，供 LLM 选择目标。"""

    tool_name: str = "send_to_list_groups"
    tool_description: str = "列出 bot 可见的群聊，可按群名或 group_id 模糊过滤。"

    async def execute(
        self,
        platform: Annotated[str, "平台标识。留空则不限平台"] = "",
        name_keyword: Annotated[str, "群名或 group_id 关键词"] = "",
        limit: Annotated[int, "最多返回多少个群，1-100，默认 30"] = 30,
    ) -> tuple[bool, str | dict[str, Any]]:
        normalized_platform = str(platform or "").strip()
        normalized_keyword = str(name_keyword or "").strip().lower()
        normalized_limit = _clamp(limit or 30, lo=1, hi=100)

        qb = QueryBuilder(ChatStreams).filter(chat_type="group")
        if normalized_platform:
            qb = qb.filter(platform=normalized_platform)

        fetch_limit = normalized_limit if not normalized_keyword else max(normalized_limit * 5, 200)
        rows = await qb.order_by("-last_active_time").limit(fetch_limit).all()
        streams = cast(list[ChatStreams], rows)

        items: list[dict[str, Any]] = []
        for stream in streams:
            gid = str(getattr(stream, "group_id", "") or "").strip()
            gname = str(getattr(stream, "group_name", "") or "").strip()
            if not gid:
                continue
            if normalized_keyword and normalized_keyword not in gname.lower() and normalized_keyword not in gid.lower():
                continue
            items.append({
                "group_id": gid,
                "group_name": gname or None,
                "platform": str(getattr(stream, "platform", "") or ""),
                "last_active_time": getattr(stream, "last_active_time", None),
            })
            if len(items) >= normalized_limit:
                break

        return True, {
            "action": "send_to_list_groups",
            "query": {
                "platform": normalized_platform or None,
                "name_keyword": normalized_keyword or None,
                "limit": normalized_limit,
            },
            "total": len(items),
            "groups": items,
            "hint": "拿到候选后调用 send_to(target_type='group', group_id=<选中的>)。",
        }


class ListUsersTool(BaseTool):
    """列出 bot 已知用户，供 LLM 选择私聊目标。"""

    tool_name: str = "send_to_list_users"
    tool_description: str = "列出 bot 已知用户，可按昵称、群名片或 user_id 模糊过滤。"

    async def execute(
        self,
        platform: Annotated[str, "平台标识。留空则不限平台"] = "",
        name_keyword: Annotated[str, "用户昵称、群名片或 user_id 关键词"] = "",
        limit: Annotated[int, "最多返回多少个用户，1-100，默认 30"] = 30,
    ) -> tuple[bool, str | dict[str, Any]]:
        normalized_platform = str(platform or "").strip()
        normalized_keyword = str(name_keyword or "").strip().lower()
        normalized_limit = _clamp(limit or 30, lo=1, hi=100)

        qb = QueryBuilder(PersonInfo)
        if normalized_platform:
            qb = qb.filter(platform=normalized_platform)

        fetch_limit = normalized_limit if not normalized_keyword else max(normalized_limit * 5, 200)
        rows = await qb.order_by("-last_interaction").limit(fetch_limit).all()
        persons = cast(list[PersonInfo], rows)

        items: list[dict[str, Any]] = []
        for person in persons:
            uid = str(getattr(person, "user_id", "") or "").strip()
            nickname = str(getattr(person, "nickname", "") or "").strip()
            cardname = str(getattr(person, "cardname", "") or "").strip()
            if not uid:
                continue
            if normalized_keyword and not (
                normalized_keyword in uid.lower()
                or normalized_keyword in nickname.lower()
                or normalized_keyword in cardname.lower()
            ):
                continue
            items.append({
                "user_id": uid,
                "nickname": nickname or None,
                "cardname": cardname or None,
                "platform": str(getattr(person, "platform", "") or ""),
                "last_interaction": getattr(person, "last_interaction", None),
                "interaction_count": getattr(person, "interaction_count", None),
            })
            if len(items) >= normalized_limit:
                break

        return True, {
            "action": "send_to_list_users",
            "query": {
                "platform": normalized_platform or None,
                "name_keyword": normalized_keyword or None,
                "limit": normalized_limit,
            },
            "total": len(items),
            "users": items,
            "hint": "拿到候选后调用 send_to(target_type='private', user_id=<选中的>)。",
        }


class LookupUsersTool(BaseTool):
    """按昵称/群名片查找用户候选。"""

    tool_name: str = "send_to_lookup_users"
    tool_description: str = "在 PersonInfo 中按昵称/群名片查找用户候选。"

    async def execute(
        self,
        keyword: Annotated[str, "昵称或群名片关键词"],
        platform: Annotated[str, "平台标识。留空则使用当前对话平台"] = "",
        limit: Annotated[int, "最多返回多少个用户，1-50，默认 20"] = 20,
    ) -> tuple[bool, str | dict[str, Any]]:
        normalized_keyword = str(keyword or "").strip()
        if not normalized_keyword:
            return False, "keyword 不能为空"
        normalized_platform = str(platform or "").strip()
        normalized_limit = _clamp(limit or 20, lo=1, hi=50)
        combined = await _lookup_user_candidates(normalized_platform, normalized_keyword, normalized_limit)
        return True, {
            "action": "send_to_lookup_users",
            "query": {
                "keyword": normalized_keyword,
                "platform": normalized_platform or None,
                "limit": normalized_limit,
            },
            "total": len(combined),
            "users": combined,
            "hint": "拿到候选后调用 send_to(target_type='private', user_id=<选中的>)。",
        }


class SendToStreamContextTool(BaseTool):
    """查询其他聊天流的原始上下文。"""

    tool_name: str = "send_to_get_stream_context"
    tool_description: str = "按聊天流名称、索引或 stream_id 查询其他聊天流最近原始消息和摘要。"
    chatter_allow: list[str] = []

    async def execute(
        self,
        stream_identifier: Annotated[str, "目标聊天流名称、索引号或 stream_id"],
        message_count: Annotated[int, "消息数量，默认 20，最多 200"] = 20,
    ) -> tuple[bool, str]:
        from src.app.plugin_system.api import message_api
        from .stream_index import _build_stream_title, list_summary_records

        if message_count < 1:
            return False, "消息数量必须大于 0"
        if message_count > 200:
            return False, "单次最多获取 200 条消息"
        msg_count = message_count  # 边界已检查，无需 _clamp
        ident = str(stream_identifier or "").strip()
        if not ident:
            return False, "stream_identifier 不能为空"
        records = await list_summary_records(self.plugin)
        if not records:
            return False, "当前没有任何聊天流记录"

        target_record = None
        if ident.isdigit() and len(ident) <= 3:
            index = int(ident) - 1
            if 0 <= index < len(records):
                target_record = records[index]
            else:
                return False, f"索引号 {ident} 超出范围（共 {len(records)} 条记录）"
        else:
            matched = [
                r for r in records
                if ident.lower() in _build_stream_title(r).lower() or ident == r.stream_id
            ]
            if not matched:
                return False, f"未找到匹配 '{ident}' 的聊天流"
            if len(matched) > 1:
                lines = [f"找到 {len(matched)} 个匹配的聊天流，请更具体："]
                for idx, record in enumerate(matched[:5], start=1):
                    lines.append(f"{idx}. {_build_stream_title(record)} [{record.platform}:{record.chat_type}]")
                return False, "\n".join(lines)
            target_record = matched[0]

        messages = await message_api.get_recent_messages(
            stream_id=target_record.stream_id,
            hours=24 * 365,
            limit=msg_count,
            limit_mode="latest",
            filter_bot=False,
        )
        lines = [
            f"聊天流: {_build_stream_title(target_record)}",
            f"平台: {target_record.platform or 'unknown'}",
            f"类型: {target_record.chat_type or 'unknown'}",
            f"Stream ID: {target_record.stream_id}",
            "",
            f"最近 {len(messages)} 条原始聊天记录:",
            "",
        ]
        if messages:
            lines.append(await message_api.build_readable_messages_to_str(
                messages=messages,
                replace_bot_name=False,
                merge_messages=False,
                timestamp_mode="absolute",
                truncate=False,
            ))
        else:
            lines.append("该聊天流暂无历史消息记录")
        lines.extend(["", "当前摘要:", target_record.summary])
        return True, "\n".join(lines)


class SendToDailyMemoryTool(BaseTool):
    """查询群聊每日短期记忆。"""

    tool_name: str = "send_to_get_daily_memory"
    tool_description: str = "查询某个群最近若干天的短期记忆（日总结），用于补全摘要细节。"
    chatter_allow: list[str] = []

    async def execute(
        self,
        stream_identifier: Annotated[str, "目标群标识：群名、索引号或群号"],
        days: Annotated[int, "查询最近天数，默认 1"] = 1,
        date_filter: Annotated[str, "指定日期 YYYY-MM-DD"] = "",
    ) -> tuple[bool, str]:
        from datetime import date, datetime, timedelta

        from .daily_memory import get_memory, list_recent_memories
        from .stream_index import StreamSummaryRecord, _build_stream_title, list_summary_records

        config = self.plugin.config if isinstance(self.plugin.config, SendToConfig) else SendToConfig()
        if not config.daily_memory.enabled:
            return False, "短期记忆功能已禁用"
        ident = str(stream_identifier or "").strip()
        if not ident:
            return False, "请提供有效的群标识符"
        records = await list_summary_records(self.plugin)
        target: StreamSummaryRecord | None = None
        if ident.isdigit() and len(ident) <= 3:
            idx = int(ident) - 1
            if 0 <= idx < len(records):
                target = records[idx]
        if target is None:
            target = next(
                (r for r in records if r.chat_type == "group" and r.target_id and str(r.target_id) == ident),
                None,
            )
        if target is None:
            matched = [
                r for r in records
                if r.chat_type == "group" and ident.lower() in _build_stream_title(r).lower()
            ]
            if len(matched) == 1:
                target = matched[0]
            elif len(matched) > 1:
                hint_lines = [f"找到 {len(matched)} 个匹配的群，请更精确："]
                for idx, r in enumerate(matched[:6], start=1):
                    hint_lines.append(f"{idx}. {_build_stream_title(r)} (群号 {r.target_id or '未知'})")
                return False, "\n".join(hint_lines)
        if target is None:
            return False, f"未找到匹配 '{ident}' 的群聊"
        if target.chat_type != "group":
            return False, f"短期记忆仅支持群聊，目标 {_build_stream_title(target)} 不是群聊"

        max_query_days = max(1, int(config.daily_memory.max_query_days))
        if date_filter.strip():
            requested = date_filter.strip()
            try:
                parsed = datetime.strptime(requested, "%Y-%m-%d").date()
            except ValueError:
                return False, f"日期格式不正确：{requested}（应为 YYYY-MM-DD）"
            today = date.today()
            earliest = today - timedelta(days=max_query_days - 1)
            if parsed > today or parsed < earliest:
                return False, f"该日期 {requested} 不在可查范围内（{earliest.isoformat()} ~ {today.isoformat()}）"
            records_daily = [
                r for r in [await get_memory(self.plugin, target.stream_id, requested)]
                if r is not None
            ]
        else:
            records_daily = await list_recent_memories(
                self.plugin, target.stream_id, max(1, min(int(days), max_query_days))
            )
        if not records_daily:
            return False, f"{_build_stream_title(target)} 没有可用短期记忆"

        lines = [
            f"群聊: {_build_stream_title(target)}",
            f"群号: {target.target_id or '未知'}",
            f"平台: {target.platform or 'unknown'}",
            "",
        ]
        for record in records_daily:
            lines.extend([
                f"── {record.memory_date} ──",
                f"消息总数: {record.message_count}    更新于: {record.updated_at}",
                "",
                record.summary,
                "",
            ])
        return True, "\n".join(lines).rstrip()


class SendToFindStreamTool(BaseTool):
    """把名称、索引、QQ号、群号反查为目标流参数。"""

    tool_name: str = "send_to_find_stream"
    tool_description: str = "跨流发送/转告前置查找工具，返回 stream_id、platform、chat_type、target_id 和摘要。"
    chatter_allow: list[str] = []

    async def execute(
        self,
        identifier: Annotated[str, "目标流标识：名称、索引号、QQ号、群号或 stream_id"],
        chat_type_hint: Annotated[str, "可选消歧：private/group"] = "",
    ) -> tuple[bool, str]:
        from src.app.plugin_system.api import stream_api
        from .stream_index import _build_stream_title, list_summary_records

        ident = str(identifier or "").strip()
        if not ident:
            return False, "请提供有效的目标流标识符"
        hint = str(chat_type_hint or "").strip().lower()
        if hint not in ("private", "group"):
            hint = ""
        records = await list_summary_records(self.plugin)
        candidates = [r for r in records if not hint or r.chat_type == hint]
        target = None
        if len(ident) == 64 and all(c in "0123456789abcdef" for c in ident.lower()):
            target = next((r for r in records if r.stream_id == ident), None)
            if target is None:
                info = await stream_api.get_stream_info(ident)
                if isinstance(info, dict):
                    return True, "\n".join([
                        "找到目标流（来自 StreamManager 但暂无摘要）：",
                        f"target_stream_id: \"{ident}\"",
                        f"target_platform: \"{info.get('platform') or ''}\"",
                        f"chat_type: {info.get('chat_type') or 'unknown'}",
                        f"target_group_id: \"{info.get('group_id') or ''}\"",
                        "summary: (暂无摘要)",
                    ])
        elif ident.isdigit() and len(ident) <= 3:
            idx = int(ident) - 1
            if 0 <= idx < len(candidates):
                target = candidates[idx]
        elif ident.isdigit():
            target = next((r for r in candidates if r.target_id and str(r.target_id) == ident), None)
            # 纯数字标识符在摘要记录中找不到时，尝试按 QQ 号/群号直接生成流
            if target is None:
                from src.core.models.stream import ChatStream
                results: list[tuple[str, str, str]] = []  # (stream_id, chat_type, target_id)
                types_to_try = [hint] if hint else ["private", "group"]
                for try_type in types_to_try:
                    if try_type == "group":
                        sid = ChatStream.generate_stream_id("qq", group_id=ident)
                    else:
                        sid = ChatStream.generate_stream_id("qq", user_id=ident)
                    results.append((sid, try_type, ident))
                # 若有 chat_type_hint 则只返回一种；否则两种都尝试
                if len(results) == 1:
                    sid, ctype, tid = results[0]
                    try:
                        kwargs: dict[str, Any] = {
                            "stream_id": sid,
                            "platform": "qq",
                            "chat_type": ctype,
                        }
                        if ctype == "group":
                            kwargs["group_id"] = tid
                        else:
                            kwargs["user_id"] = tid
                        await stream_api.get_or_create_stream(**kwargs)
                    except Exception:
                        pass
                    return True, "\n".join([
                        "找到目标流（按 ID 直接生成，暂无摘要）：",
                        f"target_stream_id: \"{sid}\"",
                        "target_platform: \"qq\"",
                        f"chat_type: {ctype}",
                        f"target_{'group' if ctype == 'group' else 'user'}_id: \"{tid}\"",
                        "summary: (暂无摘要)",
                    ])
                # 无 hint 时两种都可能，返回歧义提示
                return False, "\n".join([
                    f"数字标识 '{ident}' 可能是群号也可能是 QQ 号，请通过 chat_type_hint 消歧：",
                    f"- 若为群号：chat_type_hint='group' → stream_id=\"{results[1][0]}\"",
                    f"- 若为 QQ 号：chat_type_hint='private' → stream_id=\"{results[0][0]}\"",
                ])
        else:
            matched = [
                r for r in candidates
                if ident.lower() in _build_stream_title(r).lower()
            ]
            if len(matched) == 1:
                target = matched[0]
            elif len(matched) > 1:
                hint_lines = [f"找到 {len(matched)} 个匹配的聊天流，请进一步精确："]
                for idx, r in enumerate(matched[:8], start=1):
                    hint_lines.append(
                        f"{idx}. {_build_stream_title(r)} [{r.platform}:{r.chat_type}] (id {r.target_id or '未知'})"
                    )
                return False, "\n".join(hint_lines)
        if target is None:
            return False, f"未找到匹配 '{ident}' 的聊天流。可改用 send_to_list_groups/send_to_lookup_users。"
        try:
            in_memory = target.stream_id in stream_api.get_all_stream_ids()
        except Exception:
            in_memory = False
        lines = [
            "找到目标流，可用于 send_to_relay_intent 或 send_to：",
            "",
            "【下一步调用参数建议】",
            f"- target_stream_id: \"{target.stream_id}\"",
            f"- target_platform: \"{target.platform or 'qq'}\"",
            f"- target_user_id: \"{target.target_id if target.chat_type == 'private' else ''}\"",
            f"- target_group_id: \"{target.target_id if target.chat_type == 'group' else ''}\"",
            "",
            "【目标流详情】",
            f"名称: {_build_stream_title(target)}",
            f"platform: {target.platform or 'unknown'}",
            f"chat_type: {target.chat_type or 'unknown'}",
            f"is_in_memory: {bool(in_memory)}",
            f"updated_at: {target.updated_at or 'unknown'}",
            "",
            "【当前摘要】",
            target.summary or "(暂无摘要)",
        ]
        return True, "\n".join(lines)


class SendToUserMemoryTool(BaseTool):
    """从长期记忆中检索用户相关内容。"""

    tool_name: str = "send_to_lookup_user_memory"
    tool_description: str = "按平台用户检索长期记忆，用于跨私聊/群聊了解同一用户背景和事件。"

    async def execute(self, platform: Annotated[str, "平台标识"], user_id: Annotated[str, "目标用户 ID"] = "", user_hint: Annotated[str, "用户昵称/群名片"] = "", query: Annotated[str, "检索关键词"] = "", top_n: Annotated[int, "最多返回数量"] = 0, include_archived: Annotated[bool, "是否包含归档"] = False) -> tuple[bool, str | dict[str, Any]]:
        from .context_lookup import lookup_user_memory

        return await lookup_user_memory(self.plugin, platform=platform, user_id=user_id, user_hint=user_hint, query=query, top_n=top_n, include_archived=include_archived)


class SendToUserContextTool(BaseTool):
    """从聊天流中抓取用户相关上下文。"""

    tool_name: str = "send_to_lookup_user_context"
    tool_description: str = "按平台用户从私聊与群聊聊天流中抓取近期上下文，标注 target_user/bot/other。"

    async def execute(self, platform: Annotated[str, "平台标识"], user_id: Annotated[str, "目标用户 ID"] = "", user_hint: Annotated[str, "用户昵称/群名片"] = "", chat_scope: Annotated[str, "private/group/all"] = "all", group_id: Annotated[str, "限定群 ID"] = "", per_stream_limit: Annotated[int, "每流消息数"] = 0, max_streams: Annotated[int, "最大流数量"] = 0, around_user: Annotated[bool, "围绕用户发言截取"] = True, max_chars_per_message: Annotated[int, "单条消息长度"] = 0) -> tuple[bool, str | dict[str, Any]]:
        from .context_lookup import lookup_user_context

        return await lookup_user_context(self.plugin, platform=platform, user_id=user_id, user_hint=user_hint, chat_scope=chat_scope, group_id=group_id, per_stream_limit=per_stream_limit, max_streams=max_streams, around_user=around_user, max_chars_per_message=max_chars_per_message)


__all__ = [
    "ListGroupsTool",
    "ListUsersTool",
    "LookupUsersTool",
    "SendToStreamContextTool",
    "SendToDailyMemoryTool",
    "SendToFindStreamTool",
    "SendToUserMemoryTool",
    "SendToUserContextTool",
]
