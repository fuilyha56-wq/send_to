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
    plugin_version: str = "3.0.3-alpha"

    configs: list[type] = [SendToConfig]
    dependent_components: list[str] = []

    _archive_stop_event: asyncio.Event | None = None

    def get_components(self) -> list[type]:
        """根据配置返回组件列表。"""

        config = self.config if isinstance(self.config, SendToConfig) else SendToConfig()
        if not config.plugin.enabled:
            logger.info("send_to 已在配置中禁用")
            return []

        components: list[type] = []
        if config.dispatch.enable_send_text:
            components.append(SendToAction)
        if config.dispatch.enable_execute_action:
            components.append(SendToExecuteAction)
        if config.dispatch.enable_target_tools:
            components.extend([ListGroupsTool, ListUsersTool, LookupUsersTool])

        if config.index.enabled:
            components.extend([SendToSummaryUpdateAction, SendToAutoSummaryHandler])
        if config.lookup.enable_stream_context:
            components.append(SendToStreamContextTool)
        if config.lookup.enable_daily_memory:
            components.append(SendToDailyMemoryTool)
        if config.lookup.enable_find_stream:
            components.append(SendToFindStreamTool)
        if config.lookup.enable_user_memory:
            components.append(SendToUserMemoryTool)
        if config.lookup.enable_user_context:
            components.append(SendToUserContextTool)

        if config.daily_memory.enabled and config.daily_memory.enable_command:
            components.append(SendToMemoryCommand)
        if config.relay.enabled:
            components.append(SendToRelayIntentAction)
        if config.relay.enable_debug_command:
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
        """插件卸载时停止归档循环并清理 reminder。"""

        if self._archive_stop_event is not None:
            self._archive_stop_event.set()
            self._archive_stop_event = None
        try:
            get_system_reminder_store().delete(ACTOR_REMINDER_BUCKET, ACTOR_REMINDER_NAME)
        except Exception as error:
            logger.debug(f"清理 send_to reminder 失败（可忽略）: {error}")
