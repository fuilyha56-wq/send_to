"""send_to 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


_DEFAULT_WANDER_SYSTEM = """\
# 角色
你是 Bot 的“是否要串门”决策模块。给你一段当前观察到的群消息上下文，以及若干候选目标群/私聊，
你要判断 Bot 是否要主动跑去某个候选地点搭话。

# 重要心态
默认行为是 go=false。只有当前话题与候选目标真正相关、发言自然且有实质内容时才允许 go=true。

# 严格禁忌
- 日常寒暄、表情、单字、哈哈等低信息量内容
- 冷场群、跨平台、隐私八卦、机械复述或自我介绍
- 任何会让用户觉得突兀/扰民的发言

# 输出格式
{
  "go": false,
  "target_stream_id": null,
  "content": null,
  "why": "用一句话说明决策理由"
}
"""


class SendToConfig(BaseConfig):
    """send_to 综合跨流插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "send_to 跨聊天流发送、上下文、记忆与转告配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件主配置。"""

        enabled: bool = Field(default=True, description="是否启用 send_to 插件")
        version: str = Field(default="3.0.2", description="插件版本", disabled=True)

    @config_section("dispatch", title="跨流发送", tag="plugin")
    class DispatchSection(SectionBase):
        """跨流发送和执行配置。"""

        enable_send_text: bool = Field(default=True, description="启用 send_to 轻量跨流文本发送")
        enable_execute_action: bool = Field(default=False, description="启用 send_to_execute 跨流执行 Action（默认关闭）")
        enable_target_tools: bool = Field(default=True, description="启用群/用户列表与查找工具")

    @config_section("relay", title="跨流转告", tag="plugin")
    class RelaySection(SectionBase):
        """跨流虚拟消息转告配置。"""

        enabled: bool = Field(default=False, description="启用 send_to_relay_intent 意识迁移/转告（默认关闭）")
        max_relay_chars: int = Field(default=4000, description="单次转告内容最大字符数")
        allow_self_relay: bool = Field(default=False, description="是否允许转告到当前流")
        default_target_platform: str = Field(default="qq", description="未显式提供平台时使用的默认平台")
        include_context_messages_default: int = Field(default=10, description="默认携带的源流上下文条数")
        include_context_messages_max: int = Field(default=50, description="最多携带的源流上下文条数")
        enable_debug_command: bool = Field(default=False, description="启用 relay 调试命令（默认关闭）")

    @config_section("index", title="跨流摘要", tag="ai")
    class IndexSection(SectionBase):
        """跨流摘要配置。"""

        enabled: bool = Field(default=True, description="启用跨聊天流摘要索引")
        inject_summary_reminder: bool = Field(default=False, description="将跨流摘要注入 actor reminder（默认关闭，避免动态内容降低 prompt cache 命中率）")
        auto_summary_enabled: bool = Field(default=True, description="自动按消息批次更新摘要")
        auto_summary_batch_size: int = Field(default=8, description="每累计多少条新消息触发一次摘要")
        auto_summary_task_name: str = Field(default="utils", description="自动摘要使用的模型任务名")
        visible_stream_limit: int = Field(default=12, description="reminder 中最多注入多少条流摘要")
        max_summary_chars: int = Field(default=480, description="单条摘要最大字符数")

    @config_section("daily_memory", title="每日短期记忆", tag="ai")
    class DailyMemorySection(SectionBase):
        """每日短期记忆配置。"""

        enabled: bool = Field(default=True, description="启用群聊每日短期记忆")
        trigger_rounds: int = Field(default=40, description="触发短期记忆总结的交互轮数")
        trigger_idle_seconds: int = Field(default=10800, description="触发短期记忆总结的最大空闲时间")
        task_name: str = Field(default="actor", description="生成短期记忆使用的模型任务名")
        max_query_days: int = Field(default=3, description="工具允许查询最近多少天")
        inject_into_reminder: bool = Field(default=False, description="将本群今日短期记忆注入固定 reminder（默认关闭，建议通过 auto_inject 按轮注入）")
        max_summary_chars: int = Field(default=1400, description="单日短期记忆最大字符数")
        archive_check_interval_seconds: int = Field(default=60, description="跨天归档扫描间隔")
        enable_command: bool = Field(default=True, description="启用 send_to_memory_command 强制生成命令")

    @config_section("lookup", title="查询工具", tag="tool")
    class LookupSection(SectionBase):
        """上下文和记忆查询配置。"""

        enable_stream_context: bool = Field(default=True, description="启用 send_to_get_stream_context")
        enable_daily_memory: bool = Field(default=True, description="启用 send_to_get_daily_memory")
        enable_find_stream: bool = Field(default=True, description="启用 send_to_find_stream")
        enable_user_memory: bool = Field(default=True, description="启用 send_to_lookup_user_memory")
        enable_user_context: bool = Field(default=True, description="启用 send_to_lookup_user_context")
        memory_top_n_default: int = Field(default=8, description="长期记忆默认返回条数")
        memory_top_n_max: int = Field(default=20, description="长期记忆最大返回条数")
        per_stream_limit_default: int = Field(default=20, description="每流默认消息数")
        per_stream_limit_max: int = Field(default=80, description="每流最大消息数")
        max_streams_default: int = Field(default=4, description="默认读取流数量")
        max_streams_max: int = Field(default=10, description="最大读取流数量")
        max_chars_per_message_default: int = Field(default=300, description="默认单条消息长度")
        max_chars_per_message_max: int = Field(default=1000, description="最大单条消息长度")
        candidate_stream_scan_multiplier: int = Field(default=80, description="按用户消息回溯群聊候选流的扫描倍数")
        around_user_default: bool = Field(default=True, description="默认围绕目标用户最近发言截取上下文")
        include_timeline_text: bool = Field(default=True, description="查询结果包含 timeline 文本")
        include_archived_default: bool = Field(default=False, description="长期记忆默认包含归档")
        include_knowledge_when_query: bool = Field(default=True, description="有 query 时允许检索知识类记忆")
        include_related: bool = Field(default=True, description="检索关联记忆")

    @config_section("auto_inject", title="自动跨流注入", tag="ai")
    class AutoInjectSection(SectionBase):
        """prompt 构建时自动注入跨流上下文。"""

        enabled: bool = Field(default=True, description="启用 send_to_auto_context_inject：合并注入跨流摘要和当前用户近期上下文")
        auto_discover_prompts: bool = Field(default=True, description="自动识别可注入 prompt")
        include_summary_index: bool = Field(default=True, description="自动注入时同时合并跨流摘要索引")
        summary_stream_limit: int = Field(default=6, description="自动注入时最多附带多少条跨流摘要")
        target_prompts: list[str] = Field(default_factory=list, description="手动补充 prompt 名称")
        kfc_prompts: list[str] = Field(default_factory=lambda: ["kfc_user_prompt", "NFC_user_prompt", "nfc_user_prompt"])
        per_stream_limit: int = Field(default=10, description="自动注入每流消息数")
        max_streams: int = Field(default=2, description="自动注入最大流数量")
        max_chars_per_message: int = Field(default=200, description="自动注入单条消息长度")
        cooldown_seconds: int = Field(default=30, description="同一流自动注入冷却秒数")

    @config_section("privacy", title="隐私与访问控制", tag="security")
    class PrivacySection(SectionBase):
        """隐私、黑白名单和脱敏配置。"""

        private_bridge_mode: str = Field(default="two_way", description="私聊互通模式：off/one_way/two_way")
        group_list_type: str = Field(default="blacklist", description="群聊名单模式：blacklist/whitelist")
        group_list: list[str | int] = Field(default_factory=list, description="群聊黑/白名单")
        private_list_type: str = Field(default="blacklist", description="私聊名单模式：blacklist/whitelist")
        private_list: list[str | int] = Field(default_factory=list, description="私聊黑/白名单")
        allowed_chat_scopes: list[str] = Field(default_factory=lambda: ["private", "group", "all"])
        platform_allowlist: list[str] = Field(default_factory=list)
        platform_blocklist: list[str] = Field(default_factory=list)
        user_allowlist: list[str] = Field(default_factory=list)
        user_blocklist: list[str] = Field(default_factory=list)
        group_allowlist: list[str] = Field(default_factory=list)
        group_blocklist: list[str] = Field(default_factory=list)
        require_user_identity: bool = Field(default=True, description="查询用户上下文时要求明确用户身份")
        expose_person_id: bool = Field(default=True, description="结果中暴露 person_id")
        expose_message_id: bool = Field(default=True, description="结果中暴露 message_id")
        expose_stream_id: bool = Field(default=True, description="结果中暴露 stream_id")
        include_person_profile: bool = Field(default=True, description="返回用户画像字段")

    @config_section("wander", title="自动串门", tag="ai")
    class WanderSection(SectionBase):
        """串门功能配置。"""

        enabled: bool = Field(default=False, description="是否启用自动串门（默认关闭）")
        dry_run: bool = Field(default=True, description="空跑模式：只记录决策，不真正发送")
        decision_model: str = Field(default="", description="决策模型名，留空时使用 model_tasks.sub_actor")
        pre_pass_probability: float = Field(default=0.08, description="一阶段过滤通过概率")
        global_cooldown_sec: int = Field(default=180, description="全局冷却秒数")
        per_target_cooldown_min: int = Field(default=60, description="单目标冷却分钟数")
        max_per_hour: int = Field(default=4, description="每小时最多串门次数")
        active_window_min: int = Field(default=30, description="候选目标最近活跃窗口分钟数")
        candidate_top_k: int = Field(default=5, description="候选目标数量上限")
        context_messages: int = Field(default=8, description="源流上下文消息条数")
        target_preview_messages: int = Field(default=3, description="目标预览消息条数")
        quiet_hours_start: int = Field(default=1, description="静默时段起始小时")
        quiet_hours_end: int = Field(default=7, description="静默时段结束小时")
        source_scope_mode: str = Field(default="blacklist", description="源流范围模式")
        source_groups: list[str] = Field(default_factory=list, description="源流群号列表")
        source_users: list[str] = Field(default_factory=list, description="源流私聊用户号列表")
        target_scope_mode: str = Field(default="whitelist", description="目标流范围模式")
        target_groups: list[str] = Field(default_factory=list, description="目标群号列表")
        target_users: list[str] = Field(default_factory=list, description="目标私聊用户号列表")
        allow_private_target: bool = Field(default=False, description="是否允许向私聊串门")
        decision_temperature: float = Field(default=0.2, description="决策模型温度")
        decision_max_tokens: int = Field(default=300, description="决策模型最大输出 token 数")

    @config_section("prompts", title="提示词", tag="ai")
    class PromptsSection(SectionBase):
        """提示词配置。"""

        system_prompt: str = Field(default=_DEFAULT_WANDER_SYSTEM, description="串门决策 system prompt")

    plugin: PluginSection = Field(default_factory=PluginSection)
    dispatch: DispatchSection = Field(default_factory=DispatchSection)
    relay: RelaySection = Field(default_factory=RelaySection)
    index: IndexSection = Field(default_factory=IndexSection)
    daily_memory: DailyMemorySection = Field(default_factory=DailyMemorySection)
    lookup: LookupSection = Field(default_factory=LookupSection)
    auto_inject: AutoInjectSection = Field(default_factory=AutoInjectSection)
    privacy: PrivacySection = Field(default_factory=PrivacySection)
    wander: WanderSection = Field(default_factory=WanderSection)
    prompts: PromptsSection = Field(default_factory=PromptsSection)
