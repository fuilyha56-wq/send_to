"""send_to 查询工具：给 LLM 用来浏览可用的群/用户列表，辅助定位目标。

配合 send_to Action：当 hint 歧义或不确定时，LM 可以先调用这两个 Tool
把候选列表拿到上下文，自己判断要发到哪个 ID。
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from src.core.components import BaseTool
from src.core.models.sql_alchemy import ChatStreams, PersonInfo
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

from .action import _lookup_user_candidates

logger = get_logger("send_to.tools")


def _clamp(value: int, *, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(hi, v))


class ListGroupsTool(BaseTool):
    """列出 bot 可见的群，供 LLM 选择目标。"""

    tool_name: str = "send_to_list_groups"
    tool_description: str = (
        "列出 bot 可见的群聊（从 ChatStreams 表按 last_active_time 倒序）。"
        "可选按 name_keyword 在群名里模糊匹配。"
        "用于 send_to action 在 group_hint 歧义或未知时辅助定位 group_id。"
    )

    async def execute(
        self,
        platform: Annotated[
            str,
            "平台标识（如 qq、wechat）。留空则使用当前对话平台",
        ] = "",
        name_keyword: Annotated[
            str,
            "群名关键词（大小写不敏感的包含匹配）。留空则按活跃度返回全部",
        ] = "",
        limit: Annotated[
            int,
            "最多返回多少个群，1-100，默认 30",
        ] = 30,
    ) -> tuple[bool, str | dict[str, Any]]:
        normalized_platform = str(platform or "").strip()
        normalized_keyword = str(name_keyword or "").strip().lower()
        normalized_limit = _clamp(limit or 30, lo=1, hi=100)

        qb = QueryBuilder(ChatStreams).filter(chat_type="group")
        if normalized_platform:
            qb = qb.filter(platform=normalized_platform)

        # 先多拿一点，再按 name 过滤
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

            items.append(
                {
                    "group_id": gid,
                    "group_name": gname or None,
                    "platform": str(getattr(stream, "platform", "") or ""),
                    "last_active_time": getattr(stream, "last_active_time", None),
                }
            )
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
            "hint": "拿到候选后调用 send_to(target_type='group', group_id=<选中的>) 发送消息。",
        }


class ListUsersTool(BaseTool):
    """列出 bot 已知用户，供 LLM 选择私聊目标。"""

    tool_name: str = "send_to_list_users"
    tool_description: str = (
        "列出 bot 已知用户（从 PersonInfo 表按 last_interaction 倒序）。"
        "可选按 name_keyword 在昵称、群名片或 user_id 中模糊匹配。"
        "用于 send_to action 在不知道 user_id 时浏览可用用户。"
    )

    async def execute(
        self,
        platform: Annotated[
            str,
            "平台标识（如 qq、wechat）。留空则返回所有平台",
        ] = "",
        name_keyword: Annotated[
            str,
            "用户昵称、群名片或 user_id 关键词（大小写不敏感包含匹配）。留空则按最近互动返回全部",
        ] = "",
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

            items.append(
                {
                    "user_id": uid,
                    "nickname": nickname or None,
                    "cardname": cardname or None,
                    "platform": str(getattr(person, "platform", "") or ""),
                    "last_interaction": getattr(person, "last_interaction", None),
                    "interaction_count": getattr(person, "interaction_count", None),
                }
            )
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
            "hint": "拿到候选后调用 send_to(target_type='private', user_id=<选中的>) 发送消息。",
        }


class LookupUsersTool(BaseTool):
    """按昵称/群名片查找用户候选。"""

    tool_name: str = "send_to_lookup_users"
    tool_description: str = (
        "在 PersonInfo 中按昵称/群名片查找用户候选，返回匹配列表。"
        "当 user_hint 歧义时，LM 可先调用此工具让用户确认要发给哪位。"
    )

    async def execute(
        self,
        keyword: Annotated[str, "昵称或群名片关键词（大小写不敏感的包含匹配）"],
        platform: Annotated[
            str,
            "平台标识（如 qq、wechat）。留空则使用当前对话平台",
        ] = "",
        limit: Annotated[int, "最多返回多少个用户，1-50，默认 20"] = 20,
    ) -> tuple[bool, str | dict[str, Any]]:
        normalized_keyword = str(keyword or "").strip()
        if not normalized_keyword:
            return False, "keyword 不能为空"

        normalized_platform = str(platform or "").strip()
        normalized_limit = _clamp(limit or 20, lo=1, hi=50)

        combined = await _lookup_user_candidates(
            normalized_platform,
            normalized_keyword,
            normalized_limit,
        )

        return True, {
            "action": "send_to_lookup_users",
            "query": {
                "keyword": normalized_keyword,
                "platform": normalized_platform or None,
                "limit": normalized_limit,
            },
            "total": len(combined),
            "users": combined,
            "hint": (
                "拿到候选后调用 send_to(target_type='private', user_id=<选中的>) 发送消息。"
                "若候选多且都疑似，建议先问用户选哪位。"
            ),
        }


__all__ = ["ListGroupsTool", "ListUsersTool", "LookupUsersTool"]
