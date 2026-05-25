"""send_to 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .action import SendToAction, SendToExecuteAction
from .config import SendToConfig
from .tools import ListGroupsTool, ListUsersTool, LookupUsersTool
from .wander import WanderEventHandler

logger = get_logger("send_to")


@register_plugin
class SendToPlugin(BasePlugin):
    """跨聊天流发送消息/执行动作插件。

    提供：
    - send_to Action：让 LLM 主动把消息发到其他聊天流
    - send_to_execute Action：让 LLM 在目标聊天流中执行指定动作
    - send_to_list_groups Tool：列出可用群，辅助定位 group_id
    - send_to_list_users Tool：列出已知用户，辅助定位 user_id
    - send_to_lookup_users Tool：查找用户候选，辅助定位 user_id
    - send_to_wander EventHandler（可选）：监听消息，由 sub_actor 决策是否主动串门
    """

    plugin_name: str = "send_to"
    plugin_description: str = (
        "让 LLM 主动将消息发送到其他聊天流（群/私聊），或在目标流中执行动作，"
        "支持被启发式串门"
    )
    plugin_version: str = "2.0.0"

    configs: list[type] = [SendToConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        components: list[type] = [
            SendToAction,
            SendToExecuteAction,
            ListGroupsTool,
            ListUsersTool,
            LookupUsersTool,
        ]

        config = self.config
        if isinstance(config, SendToConfig) and config.wander.enabled:
            components.append(WanderEventHandler)

        return components
