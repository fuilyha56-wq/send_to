"""send_to 事件处理器。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType, Message
from src.kernel.concurrency import get_task_manager
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .config import SendToConfig
from .daily_memory import register_bot_message, register_inbound_message
from .stream_index import collect_message_for_auto_summary, sync_actor_reminder

logger = get_logger("send_to.event_handler")


class SendToAutoSummaryHandler(BaseEventHandler):
    """收集消息并按批次触发自动摘要，同时驱动每日短期记忆。"""

    handler_name: str = "send_to_auto_summary"
    handler_description: str = "按消息批次自动刷新跨流摘要，并按轮次/闲置时间触发每日短期记忆"
    weight: int = 0
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED, EventType.ON_MESSAGE_SENT]

    async def execute(self, event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        config = self.plugin.config if isinstance(self.plugin.config, SendToConfig) else SendToConfig()
        if not config.plugin.enabled or not config.index.enabled:
            return EventDecision.SUCCESS, params

        message = params.get("message")
        if not isinstance(message, Message):
            return EventDecision.SUCCESS, params

        await sync_actor_reminder(
            self.plugin,
            current_chat_type=message.chat_type,
            current_stream_id=str(message.stream_id or ""),
        )
        direction = "outbound" if event_name == EventType.ON_MESSAGE_SENT.value else "inbound"

        async def _run_summary() -> None:
            try:
                if config.index.auto_summary_enabled:
                    await collect_message_for_auto_summary(self.plugin, message, direction=direction)
            except Exception as error:
                logger.error(f"自动摘要更新失败: stream_id={message.stream_id}, error={error}", exc_info=True)

        async def _run_daily_memory() -> None:
            try:
                if direction == "outbound":
                    await register_bot_message(self.plugin, message)
                    await sync_actor_reminder(
                        self.plugin,
                        current_chat_type=message.chat_type,
                        current_stream_id=str(message.stream_id or ""),
                    )
                else:
                    await register_inbound_message(self.plugin, message)
            except Exception as error:
                logger.error(f"每日短期记忆更新失败: stream_id={message.stream_id}, error={error}", exc_info=True)

        stream_short = (str(message.stream_id) or "unknown")[:8]
        task_manager = get_task_manager()
        task_manager.create_task(_run_summary(), name=f"send_to_auto_summary_{stream_short}", daemon=True)
        task_manager.create_task(_run_daily_memory(), name=f"send_to_daily_memory_{stream_short}", daemon=True)
        return EventDecision.SUCCESS, params
