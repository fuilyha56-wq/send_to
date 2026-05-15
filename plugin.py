"""send_to 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .action import SendToAction
from .tools import ListGroupsTool, LookupUsersTool

logger = get_logger("send_to")


@register_plugin
class SendToPlugin(BasePlugin):
    """跨聊天流发送消息插件。

    提供：
    - send_to Action：让 LLM 主动把消息发到其他聊天流
    - send_to_list_groups Tool：列出可用群，辅助定位 group_id
    - send_to_lookup_users Tool：查找用户候选，辅助定位 user_id
    """

    plugin_name: str = "send_to"
    plugin_description: str = "让 LLM 主动将消息发送到其他聊天流（群/私聊）"
    plugin_version: str = "1.0.0"

    configs: list[type] = []
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        return [SendToAction, ListGroupsTool, LookupUsersTool]
