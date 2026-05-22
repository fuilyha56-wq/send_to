"""send_to 插件的共用解析函数。

从 action.py 抽出，供 action 和 wander 共用。
"""

from __future__ import annotations

from typing import cast

from src.core.models.sql_alchemy import ChatStreams
from src.kernel.db import QueryBuilder


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
