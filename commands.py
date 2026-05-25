"""send_to 命令组件。"""

from __future__ import annotations

from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel
from src.kernel.logger import get_logger

from .daily_memory import force_generate_today

logger = get_logger("send_to.commands")


class SendToMemoryCommand(BaseCommand):
    """手动强制生成当前群当日短期记忆。"""

    command_name: str = "send_to_memory"
    command_description: str = "立即重新生成本群当日短期记忆（仅主人可用）"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配中英文命令别名。"""

        if not parts:
            return 0
        if parts[0] in ("send_to_memory", "短期记忆", "short_memory"):
            return 1
        return 0

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    def _current_chat_type(self) -> str:
        if self._message is None:
            return ""
        chat_type = self._message.extra.get("chat_type") if isinstance(self._message.extra, dict) else None
        return str(chat_type or "")

    async def _do_force_generate(self) -> tuple[bool, str]:
        chat_type = self._current_chat_type()
        if chat_type and chat_type != "group":
            await self._reply("短期记忆功能仅在群聊中可用。")
            return False, "not group"

        record = await force_generate_today(self.plugin, self.stream_id)
        if record is None:
            await self._reply("无法生成短期记忆：当日无消息、群被排除或未启用此功能。")
            return False, "no record"

        preview = record.summary
        if len(preview) > 120:
            preview = preview[:117] + "..."
        await self._reply(
            f"已为本群（{record.group_name or record.group_id}）重新生成 {record.memory_date} 的短期记忆。\n"
            f"消息总数：{record.message_count}\n"
            f"摘要预览：{preview}"
        )
        logger.info(f"[command] short_memory 强制生成完成 stream={self.stream_id} date={record.memory_date}")
        return True, "ok"

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        return await self._do_force_generate()

    @cmd_route("now")
    async def handle_now(self) -> tuple[bool, str]:
        return await self._do_force_generate()

    @cmd_route("立即")
    async def handle_now_zh(self) -> tuple[bool, str]:
        return await self._do_force_generate()


class SendToRelayDebugCommand(BaseCommand):
    """send_to relay 调试命令，占位且默认关闭。"""

    command_name: str = "send_to_relay"
    command_description: str = "send_to 跨流转告调试命令（仅主人可用）"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        await self._reply(
            "/send_to_relay 调试命令：\n"
            "  /send_to_relay 状态  — 显示当前说明\n"
            "  /send_to_relay 清理  — 当前无持久化日志，无需清理\n"
            "正式跨流转告请由 LLM 调用 send_to_relay_intent Action。"
        )
        return True, "ok"

    @cmd_route("状态")
    async def handle_status_zh(self) -> tuple[bool, str]:
        await self._reply("send_to_relay_intent 默认关闭；开启后请查看终端日志中的 [relay] 记录。")
        return True, "ok"

    @cmd_route("status")
    async def handle_status_en(self) -> tuple[bool, str]:
        return await self.handle_status_zh()

    @cmd_route("清理")
    async def handle_clear_zh(self) -> tuple[bool, str]:
        await self._reply("当前未提供持久化 relay 日志，无需清理。")
        return True, "ok"

    @cmd_route("clear")
    async def handle_clear_en(self) -> tuple[bool, str]:
        return await self.handle_clear_zh()
