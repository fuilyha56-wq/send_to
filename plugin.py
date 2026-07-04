"""send_to 插件入口。"""

from __future__ import annotations

import asyncio

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.core.prompt import get_system_reminder_store
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

from .action import (
    SendToAction,
    SendToExecuteAction,
    SendToRelayIntentAction,
    SendToSummaryUpdateAction,
)
from .commands import SendToMemoryCommand, SendToRelayDebugCommand
from .config import SendToConfig
from .daily_memory import run_archive_loop
from .event_handler import SendToAutoSummaryHandler
from .service import SendToService
from .stream_index import ACTOR_REMINDER_BUCKET, ACTOR_REMINDER_NAME, sync_actor_reminder
from .tools import (
    ListGroupsTool,
    ListUsersTool,
    LookupUsersTool,
    SendToDailyMemoryTool,
    SendToFindStreamTool,
    SendToStreamContextTool,
    SendToUserContextTool,
    SendToUserMemoryTool,
)
from .wander import WanderEventHandler

logger = get_logger("send_to")


@register_plugin
class SendToPlugin(BasePlugin):
    """跨聊天流发送、上下文、记忆和转告插件。"""

    plugin_name: str = "send_to"
    plugin_description: str = "跨聊天流发送、执行、上下文索引、短期记忆、relay 转告与可选自动注入"
    plugin_version: str = "3.0.7"

    configs: list[type] = [SendToConfig]
    dependent_components: list[str] = []

    _archive_stop_event: asyncio.Event | None = None
    # 已注册的 UnifiedScheduler task_name 列表，卸载时按名清理避免闭包泄漏
    _registered_schedules: list[str]

    def get_components(self) -> list[type]:
        """根据配置返回组件列表。"""

        config = self.config if isinstance(self.config, SendToConfig) else SendToConfig()
        if not config.plugin.enabled:
            logger.info("send_to 已在配置中禁用")
            return []

        components: list[type] = [SendToService]
        if config.dispatch.send_text_enabled:
            components.append(SendToAction)
        if config.dispatch.execute_action_enabled:
            components.append(SendToExecuteAction)
        if config.dispatch.target_tools_enabled:
            components.extend([ListGroupsTool, ListUsersTool, LookupUsersTool])

        if config.index.enabled:
            components.extend([SendToSummaryUpdateAction, SendToAutoSummaryHandler])
        if config.lookup.stream_context_enabled:
            components.append(SendToStreamContextTool)
        if config.lookup.daily_memory_tool_enabled:
            components.append(SendToDailyMemoryTool)
        if config.lookup.find_stream_enabled:
            components.append(SendToFindStreamTool)
        if config.lookup.user_memory_enabled:
            components.append(SendToUserMemoryTool)
        if config.lookup.user_context_enabled:
            components.append(SendToUserContextTool)

        if config.daily_memory.enabled and config.daily_memory.command_enabled:
            components.append(SendToMemoryCommand)
        if config.relay.enabled:
            components.append(SendToRelayIntentAction)
        if config.relay.debug_command_enabled:
            components.append(SendToRelayDebugCommand)
        if config.auto_inject.enabled:
            from .auto_inject import SendToAutoContextInjectHandler

            components.append(SendToAutoContextInjectHandler)
        if config.wander.enabled:
            components.append(WanderEventHandler)

        return components

    async def on_plugin_loaded(self) -> None:
        """插件加载后同步 reminder 并启动短期记忆归档循环。"""

        config = self.config if isinstance(self.config, SendToConfig) else SendToConfig()
        if not config.plugin.enabled:
            return
        # 初始化实例属性（避免可变默认值在类级共享）
        self._registered_schedules = []
        if config.index.enabled and config.index.inject_summary_reminder:
            await sync_actor_reminder(self, current_chat_type="group")
        else:
            try:
                get_system_reminder_store().delete(ACTOR_REMINDER_BUCKET, ACTOR_REMINDER_NAME)
            except Exception as error:
                logger.debug(f"清理旧 send_to reminder 失败（可忽略）: {error}")
        if config.daily_memory.enabled:
            self._archive_stop_event = asyncio.Event()
            get_task_manager().create_task(
                run_archive_loop(self, self._archive_stop_event),
                name="send_to_daily_archive_loop",
                daemon=True,
            )

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时停止归档循环、清理调度任务与 reminder。"""

        if self._archive_stop_event is not None:
            self._archive_stop_event.set()
            self._archive_stop_event = None

        # 清理 UnifiedScheduler 调度任务，避免闭包持有旧 plugin 实例导致泄漏
        schedule_names = getattr(self, "_registered_schedules", None) or []
        if schedule_names:
            try:
                from src.kernel.scheduler import get_unified_scheduler

                scheduler = get_unified_scheduler()
                for task_name in schedule_names:
                    try:
                        await scheduler.remove_schedule_by_name(task_name)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(f"清理调度任务 {task_name} 失败（可忽略）: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[brain] 调度器不可用，跳过清理: {exc}")
            self._registered_schedules = []

        try:
            get_system_reminder_store().delete(ACTOR_REMINDER_BUCKET, ACTOR_REMINDER_NAME)
        except Exception as error:
            logger.debug(f"清理 send_to reminder 失败（可忽略）: {error}")

    # ── 公开 API（供 brian_stats 等外部插件跨插件调用） ─────────────────

    async def list_recent_memories(self, stream_id: str, max_days: int):
        """读取某 stream 最近 max_days 天内的短期记忆（含今天，按日期倒序）。"""

        from .daily_memory import list_recent_memories as _impl

        return await _impl(self, stream_id, max_days)

    async def list_all_states(self):
        """枚举所有已知的 daily_state 记录。"""

        from .daily_memory import list_all_states as _impl

        return await _impl(self)

    async def get_today_memory_for_stream(self, stream_id: str):
        """读取指定 stream 今天的短期记忆。"""

        from .daily_memory import get_today_memory_for_stream as _impl

        return await _impl(self, stream_id)
