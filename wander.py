"""send_to 插件的串门事件处理器。

订阅 ON_MESSAGE_RECEIVED，按多重过滤+sub_actor 决策模型来判断
是否要主动跑去其他聊天流发言（"串门"）。

控刷屏多重保险：
1. 一阶段廉价过滤（随机概率、全局冷却、每小时上限、静默时段、源流范围）
2. 候选目标必须最近活跃 + 通过单目标冷却
3. 二阶段 sub_actor 决策（提示词倾向于让 bot 闭嘴，输出 JSON）
4. dry_run 模式只打日志不发
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

import json_repair

from src.app.plugin_system.api import llm_api, stream_api
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.types import EventType
from src.app.plugin_system.base import BaseEventHandler
from src.core.models.sql_alchemy import ChatStreams, PersonInfo
from src.kernel.db import QueryBuilder
from src.kernel.event import EventDecision
from src.app.plugin_system.types import ROLE, LLMPayload, Text
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from .config import SendToConfig

from .utils import send_streaming_text

logger = get_logger("send_to.wander")


# ── 进程内冷却状态（重启即丢失，行为更保守） ────────────────────────────────
_last_global_active_ts: float = 0.0
_last_visited_ts: dict[str, float] = {}  # stream_id -> 上次串门时间
_hourly_count: dict[str, int] = {}  # YYYYMMDDHH -> 当小时已串门次数


def _now_hour_key() -> str:
    return datetime.now().strftime("%Y%m%d%H")


def _is_in_quiet_hours(start: int, end: int) -> bool:
    """判断当前是否在静默时段（24h 制，[start, end) 区间）。"""
    if start == end:
        return False
    hour = datetime.now().hour
    if start < end:
        return start <= hour < end
    # 跨午夜（如 22 -> 6）
    return hour >= start or hour < end


def _check_scope(
    target_id: str,
    chat_type: str,
    mode: str,
    groups: list[str],
    users: list[str],
) -> bool:
    """通用范围检查（白/黑名单）。

    Args:
        target_id: 群号或用户号
        chat_type: "group" / "private"
        mode: "whitelist" / "blacklist"
        groups: 群号列表
        users: 用户号列表

    Returns:
        True 表示在范围内允许通过
    """
    mode = (mode or "whitelist").lower()
    if mode not in {"whitelist", "blacklist"}:
        mode = "whitelist"

    if chat_type == "group":
        ref = groups
    elif chat_type == "private":
        ref = users
    else:
        return False

    if mode == "whitelist":
        if not ref:
            return False  # 白名单为空 = 全不放行
        return target_id in ref
    # blacklist
    return target_id not in ref


# ── 候选目标加载 ──────────────────────────────────────────────────────────────


async def _list_candidate_targets(
    source_stream_id: str,
    platform: str,
    cfg_wander: Any,
) -> list[dict[str, Any]]:
    """拉取候选目标流列表。

    规则：
    - 同 platform
    - 最近 active_window_minutes 分钟内有活动
    - 跳过源流自身
    - 私聊默认禁用（除非 allow_private_target=True）
    - 通过 target_scope 过滤
    - 通过单目标冷却过滤
    - 取最近活跃的前 candidate_top_k 个
    """
    cutoff_ts = time.time() - cfg_wander.active_window_minutes * 60

    rows = await (
        QueryBuilder(ChatStreams)
        .filter(platform=platform)
        .order_by("-last_active_time")
        .limit(50)
        .all()
    )
    streams = cast(list[ChatStreams], rows)

    candidates: list[dict[str, Any]] = []
    cooldown_sec = cfg_wander.per_target_cooldown_minutes * 60

    for stream in streams:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid or sid == source_stream_id:
            continue

        last_active = float(getattr(stream, "last_active_time", 0) or 0)
        if last_active < cutoff_ts:
            continue

        chat_type = str(getattr(stream, "chat_type", "") or "")
        if chat_type == "private" and not cfg_wander.allow_private_target:
            continue

        # 范围过滤
        if chat_type == "group":
            target_id = str(getattr(stream, "group_id", "") or "")
        elif chat_type == "private":
            person_id = str(getattr(stream, "person_id", "") or "")
            target_id = sid
            if person_id:
                person = await QueryBuilder(PersonInfo).filter(
                    person_id=person_id,
                ).first()
                if person:
                    target_id = str(getattr(person, "user_id", "") or sid)
        else:
            continue

        if not _check_scope(
            target_id,
            chat_type,
            cfg_wander.target_scope_mode,
            cfg_wander.target_groups,
            cfg_wander.target_users,
        ):
            continue

        # 单目标冷却
        last_visit = _last_visited_ts.get(sid, 0.0)
        if time.time() - last_visit < cooldown_sec:
            continue

        candidates.append(
            {
                "stream_id": sid,
                "chat_type": chat_type,
                "group_id": str(getattr(stream, "group_id", "") or ""),
                "group_name": str(getattr(stream, "group_name", "") or ""),
                "platform": str(getattr(stream, "platform", "") or ""),
                "last_active_time": last_active,
            }
        )

        if len(candidates) >= cfg_wander.candidate_top_k:
            break

    return candidates


async def _format_messages(stream_id: str, limit: int) -> list[str]:
    """拉一段流的最近消息并格式化为简短行。"""
    try:
        msgs = await stream_api.get_stream_messages(stream_id, limit=limit)
    except Exception:
        return []

    lines: list[str] = []
    for msg in msgs:
        is_bot = getattr(msg, "sender_role", "") == "bot"
        if is_bot:
            role = "[bot]"
        else:
            sender_name = str(
                getattr(msg, "sender_name", "")
                or getattr(msg, "sender_cardname", "")
                or "未知用户"
            ).strip()
            sender_id = str(getattr(msg, "sender_id", "") or "").strip()
            # 优先用昵称，附带 ID 便于区分同名用户
            if sender_name and sender_id:
                role = f"[{sender_name}({sender_id})]"
            elif sender_name:
                role = f"[{sender_name}]"
            else:
                role = f"[用户{sender_id}]" if sender_id else "[未知用户]"

        text = str(
            getattr(msg, "processed_plain_text", None)
            or getattr(msg, "content", "")
            or ""
        ).strip()
        if not text:
            continue
        # 控制单行长度
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append(f"{role} {text}")

    return lines


# ── LLM 决策 ──────────────────────────────────────────────────────────────────


async def _llm_decide(
    config: "SendToConfig",
    source_meta: dict[str, Any],
    source_lines: list[str],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """调用 sub_actor 模型做串门决策。

    Returns:
        解析后的字典：{"go": bool, "target_stream_id": str|None,
                      "content": str|None, "why": str}，失败时返回 None。
    """
    cfg = config.wander
    sys_prompt = config.prompts.system_prompt

    # 构造 user prompt
    src_desc = (
        f"平台={source_meta.get('platform', '')}，"
        f"类型={source_meta.get('chat_type', '')}，"
        f"群名={source_meta.get('group_name', '')}"
    )

    # 候选信息（含每个目标的最近预览）
    cand_blocks: list[str] = []
    preview_results = await asyncio.gather(
        *(
            _format_messages(cand["stream_id"], cfg.target_preview_messages)
            for cand in candidates
        )
    )
    for idx, (cand, preview) in enumerate(zip(candidates, preview_results), 1):
        preview_text = "\n  ".join(preview) if preview else "（无近期消息）"
        cand_blocks.append(
            f"【候选 {idx}】stream_id={cand['stream_id']}，"
            f"群名={cand.get('group_name') or '(私聊)'}，"
            f"类型={cand['chat_type']}\n  最近消息：\n  {preview_text}"
        )

    cand_text = "\n\n".join(cand_blocks) if cand_blocks else "（无候选）"
    src_text = "\n".join(source_lines) if source_lines else "（无上下文）"

    user_prompt = (
        f"# 当前观察到的源流\n"
        f"{src_desc}\n\n"
        f"## 源流近期消息\n"
        f"{src_text}\n\n"
        f"# 候选目标\n"
        f"{cand_text}\n\n"
        f"# 任务\n"
        f"严格按 system 中的标准判断是否串门，输出 JSON。"
    )

    try:
        if cfg.decision_task_name:
            model_set = llm_api.get_model_set_by_name(
                cfg.decision_task_name,
                temperature=cfg.decision_temperature,
                max_tokens=cfg.decision_max_tokens,
            )
        else:
            model_set = llm_api.get_model_set_by_task("sub_actor")
            # 调温度（覆盖默认值），并限制输出
            model_set = [
                {
                    **entry,
                    "temperature": cfg.decision_temperature,
                    "max_tokens": cfg.decision_max_tokens,
                    "tool_call_compat": False,
                }
                for entry in model_set
            ]

        request = llm_api.create_llm_request(model_set, "send_to_wander")
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(sys_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))

        content = await send_streaming_text(request)
        if not content:
            return None

        try:
            result = json_repair.loads(content)
        except Exception:
            result = None

        if not isinstance(result, dict):
            logger.debug(f"[wander] 决策返回非 JSON：{content[:200]}")
            return None

        return {
            "go": bool(result.get("go", False)),
            "target_stream_id": (
                str(result.get("target_stream_id") or "").strip() or None
            ),
            "content": (str(result.get("content") or "").strip() or None),
            "why": str(result.get("why") or "").strip(),
        }
    except Exception as exc:
        logger.warning(f"[wander] LLM 决策异常：{exc}")
        return None


# ── EventHandler ─────────────────────────────────────────────────────────────


class WanderEventHandler(BaseEventHandler):
    """串门事件处理器：监听消息，决策是否主动去其他流发言。"""

    handler_name: str = "send_to_wander"
    handler_description: str = "观察消息时按 sub_actor 决策偶尔主动串门到其他聊天流"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED]

    def _get_config(self) -> "SendToConfig | None":
        from .config import SendToConfig

        cfg = self.plugin.config
        return cfg if isinstance(cfg, SendToConfig) else None

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """事件入口：快速过滤后派发后台任务，避免阻塞 EventBus。"""
        config = self._get_config()
        if config is None or not config.wander.enabled:
            return EventDecision.SUCCESS, params

        message = params.get("message")
        if message is None:
            return EventDecision.SUCCESS, params

        # 只处理用户消息，不对 bot 自身消息进行串门决策
        if getattr(message, "sender_role", "") == "bot":
            return EventDecision.SUCCESS, params

        stream_id = str(getattr(message, "stream_id", "") or "")
        if not stream_id:
            return EventDecision.SUCCESS, params

        text = str(
            getattr(message, "processed_plain_text", None)
            or getattr(message, "content", "")
            or ""
        ).strip()
        if not text:
            return EventDecision.SUCCESS, params

        # 同步阶段：所有不需要异步调用的廉价过滤先做
        if not self._sync_pre_filter(config, stream_id, message):
            return EventDecision.SUCCESS, params

        # 派发后台任务，避免阻塞 EventBus 5s 超时
        from src.kernel.concurrency import get_task_manager

        get_task_manager().create_task(
            self._wander_decide(config, stream_id, text)
        )
        return EventDecision.SUCCESS, params

    def _sync_pre_filter(
        self,
        config: "SendToConfig",
        stream_id: str,
        message: Any,
    ) -> bool:
        """同步、廉价的一阶段过滤。"""
        global _last_global_active_ts

        cfg = config.wander

        # 静默时段
        if _is_in_quiet_hours(cfg.quiet_hours_start, cfg.quiet_hours_end):
            return False

        # 全局冷却
        if time.time() - _last_global_active_ts < cfg.global_cooldown_seconds:
            return False

        # 每小时上限
        cur = _hourly_count.get(_now_hour_key(), 0)
        if cur >= cfg.max_per_hour:
            return False

        # 源流范围（从 message 上拿 chat_type 与 group/user id）
        chat_type = str(getattr(message, "chat_type", "") or "")
        group_id = str(getattr(message, "group_id", "") or "")
        user_id = str(getattr(message, "user_id", "") or "") or str(
            getattr(getattr(message, "user_info", None), "user_id", "") or ""
        )

        target_id = group_id if chat_type == "group" else user_id
        if not _check_scope(
            target_id,
            chat_type,
            cfg.source_scope_mode,
            cfg.source_groups,
            cfg.source_users,
        ):
            return False

        # 一阶段概率门
        if random.random() > cfg.pre_pass_probability:
            return False

        return True

    async def _wander_decide(
        self,
        config: "SendToConfig",
        source_stream_id: str,
        source_text: str,
    ) -> None:
        """后台任务：拉上下文 + 候选目标 + LLM 决策 + 发送。"""
        global _last_global_active_ts

        try:
            # 1. 源流元数据
            source_info = await stream_api.get_stream_info(source_stream_id)
            if not source_info:
                return

            platform = str(source_info.get("platform", ""))
            if not platform:
                return

            # 2. 候选目标
            candidates = await _list_candidate_targets(
                source_stream_id, platform, config.wander
            )
            if not candidates:
                logger.debug(f"[wander] 无可用候选目标 source={source_stream_id[:8]}")
                return

            # 3. 源流上下文
            source_lines = await _format_messages(
                source_stream_id, config.wander.context_messages
            )

            source_meta = {
                "platform": platform,
                "chat_type": str(source_info.get("chat_type", "")),
                "group_name": str(source_info.get("group_name", "") or ""),
            }

            # 4. LLM 决策
            decision = await _llm_decide(
                config, source_meta, source_lines, candidates
            )
            if not decision or not decision.get("go"):
                if decision:
                    logger.debug(
                        f"[wander] 决策不串门：why={decision.get('why', '')!r}"
                    )
                return

            target_stream_id = decision["target_stream_id"]
            content = decision["content"]
            if not target_stream_id or not content:
                logger.warning(
                    f"[wander] go=true 但缺少必填字段：{json.dumps(decision, ensure_ascii=False)}"
                )
                return

            # 必须在候选列表里（防止 LLM 编造 stream_id）
            valid_targets = {c["stream_id"]: c for c in candidates}
            if target_stream_id not in valid_targets:
                logger.warning(
                    f"[wander] LLM 选择的目标 {target_stream_id} 不在候选集合中，已拦截"
                )
                return

            target_meta = valid_targets[target_stream_id]

            # 5. 跨 platform 拒绝
            if str(target_meta.get("platform", "")) != platform:
                logger.warning("[wander] 跨平台串门被拒绝")
                return

            # 6. 发送
            if config.wander.dry_run:
                logger.info(
                    f"[wander][DRY_RUN] 决策串门 -> {target_stream_id} "
                    f"({target_meta.get('group_name') or target_meta.get('chat_type')}) "
                    f"内容={content!r} 理由={decision.get('why', '')!r}"
                )
            else:
                ok = await send_text(
                    content=content,
                    stream_id=target_stream_id,
                    platform=platform,
                )
                if ok:
                    logger.info(
                        f"[wander] 串门成功 -> {target_stream_id} "
                        f"内容={content!r} 理由={decision.get('why', '')!r}"
                    )
                else:
                    logger.warning(f"[wander] 串门发送失败 -> {target_stream_id}")
                    return

            # 7. 更新冷却（dry_run 也走，避免日志被同一条件刷屏）
            _last_global_active_ts = time.time()
            _last_visited_ts[target_stream_id] = time.time()
            hour_key = _now_hour_key()
            _hourly_count[hour_key] = _hourly_count.get(hour_key, 0) + 1
            # 顺手清理早于本小时的计数
            for k in list(_hourly_count.keys()):
                if k != hour_key:
                    _hourly_count.pop(k, None)

        except Exception:
            logger.exception("[wander] 后台决策任务异常")
