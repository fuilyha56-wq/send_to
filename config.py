"""send_to 插件配置。

配置文件默认路径：config/plugins/send_to/config.toml

包含十段：
- plugin：插件主开关
- dispatch：跨流发送与执行
- relay：跨流转告
- index：跨流摘要
- daily_memory：每日短期记忆
- lookup：上下文和记忆查询
- auto_inject：prompt 自动注入
- privacy：隐私、黑白名单和脱敏
- wander：串门功能
- prompts：决策提示词
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


# ── 默认串门系统提示词（重要：默认值就已经压住 bot 的串门冲动） ─────────────────
_DEFAULT_WANDER_SYSTEM = """\
# 角色
你是 Bot 的"是否要串门"决策模块。给你一段当前观察到的群消息上下文，以及若干候选目标群/私聊，
你要判断 Bot 是否要主动跑去某个候选地点搭话。

# 重要心态（最关键）
**你性格内敛，社恐，多数时候只想潜水。** 默认行为是 go=false。
只有在以下罕见条件**全部满足**时，你才允许 go=true：
1. 当前群正在讨论一个具体话题（不是闲聊"在吗""哈哈"这种）
2. 候选目标群最近的话题与之**真正相关**（不是牵强联想，不是同一个词出现就算）
3. 你能用一句话明确说出"为什么非串这一次不可" —— 含糊的理由不算
4. Bot 串过去的发言**有实质内容**（提供信息、分享见闻、衔接话题），而不是打招呼或没营养的复读

# 严格禁忌（满足任意一条立刻 go=false）
- 当前消息只是日常寒暄、表情、单字、"在吗"、"哈哈"、"嗯"等
- 候选群最近 5 分钟没人说话（冷场不要凑）
- 想串过去说的内容是问候、自我介绍、"我刚听说..."这种引出话头的废话
- 想把当前群的原文复述/转述到目标群
- 跨平台串门（platform 不一致）
- 涉及隐私、八卦、可能让人不舒服的话题

# 输出格式（必须是合法 JSON，不要包含其他文字）
{
  "go": false,
  "target_stream_id": null,
  "content": null,
  "why": "用一句话说明决策理由"
}

go=true 时 target_stream_id 必须从候选列表里挑一个，content 为要发送的文本（≤80 字，自然口吻）。
go=false 时 target_stream_id 与 content 必须为 null。

# 自检
输出前再问自己一遍："如果我是用户，看到 bot 突然在另一个群冒出这句话，会觉得自然吗？还是会觉得突兀/扰民？"
有任何一丝犹豫就 go=false。
"""


class SendToConfig(BaseConfig):
    """send_to 综合跨流插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "send_to 跨聊天流发送、上下文、记忆与转告配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件主配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 send_to 插件",
        )
        version: str = Field(
            default="3.0.9",
            description="插件版本",
            disabled=True,
        )

    @config_section("dispatch", title="跨流发送", tag="plugin")
    class DispatchSection(SectionBase):
        """跨流发送和执行配置。"""

        send_text_enabled: bool = Field(
            default=True,
            description="启用 send_to 轻量跨流文本发送",
        )
        execute_action_enabled: bool = Field(
            default=False,
            description="启用 send_to_execute 跨流执行 Action",
        )
        target_tools_enabled: bool = Field(
            default=True,
            description="启用群/用户列表与查找工具",
        )

    @config_section("relay", title="跨流转告", tag="plugin")
    class RelaySection(SectionBase):
        """跨流虚拟消息转告配置。"""

        enabled: bool = Field(
            default=False,
            description="启用 send_to_relay_intent 意识迁移/转告（默认关闭）",
        )
        max_relay_chars: int = Field(
            default=4000,
            description="单次转告内容最大字符数",
        )
        allow_self_relay: bool = Field(
            default=False,
            description="是否允许转告到当前流",
        )
        default_target_platform: str = Field(
            default="qq",
            description="未显式提供平台时使用的默认平台",
        )
        include_context_messages_default: int = Field(
            default=10,
            description="默认携带的源流上下文条数",
        )
        include_context_messages_max: int = Field(
            default=50,
            description="最多携带的源流上下文条数",
        )
        debug_command_enabled: bool = Field(
            default=False,
            description="启用 relay 调试命令",
        )

    @config_section("index", title="跨流摘要", tag="ai")
    class IndexSection(SectionBase):
        """跨流摘要配置。"""

        enabled: bool = Field(
            default=True,
            description="启用跨聊天流摘要索引",
        )
        inject_summary_reminder: bool = Field(
            default=False,
            description="将跨流摘要注入 actor reminder（避免动态内容降低 prompt cache 命中率）",
        )
        auto_summary_enabled: bool = Field(
            default=True,
            description="自动按消息批次更新摘要",
        )
        auto_summary_batch_size: int = Field(
            default=8,
            description="每累计多少条新消息触发一次摘要",
        )
        auto_summary_task_name: str = Field(
            default="utils",
            description="自动摘要使用的模型任务名",
        )
        visible_stream_limit: int = Field(
            default=12,
            description="reminder 中最多注入多少条流摘要",
        )
        max_summary_chars: int = Field(
            default=480,
            description="单条摘要最大字符数",
        )
        retention_days: int = Field(
            default=3,
            description="跨流摘要和待摘要消息的保留天数，超过后自动删除并重新积累",
        )

    @config_section("daily_memory", title="每日短期记忆", tag="ai")
    class DailyMemorySection(SectionBase):
        """每日短期记忆配置。"""

        enabled: bool = Field(
            default=True,
            description="启用群聊每日短期记忆",
        )
        trigger_rounds: int = Field(
            default=40,
            description="触发短期记忆总结的交互轮数",
        )
        trigger_idle_seconds: int = Field(
            default=10800,
            description="触发短期记忆总结的最大空闲时间",
        )
        task_name: str = Field(
            default="actor",
            description="生成短期记忆使用的模型任务名",
        )
        max_query_days: int = Field(
            default=3,
            description="工具允许查询最近多少天",
        )
        inject_into_reminder: bool = Field(
            default=False,
            description="将本群今日短期记忆注入固定 reminder（建议通过 auto_inject 按轮注入）",
        )
        max_summary_chars: int = Field(
            default=1400,
            description="单日短期记忆最大字符数",
        )
        archive_check_interval_seconds: int = Field(
            default=60,
            description="跨天归档扫描间隔",
        )
        command_enabled: bool = Field(
            default=True,
            description="启用 send_to_memory_command 强制生成命令",
        )

    @config_section("lookup", title="上下文和记忆查询", tag="tool")
    class LookupSection(SectionBase):
        """上下文和记忆查询配置。"""

        # ── 工具启用开关 ────────────────────────────────────────────────
        stream_context_enabled: bool = Field(
            default=True,
            description="启用 send_to_get_stream_context",
        )
        daily_memory_tool_enabled: bool = Field(
            default=True,
            description="启用 send_to_get_daily_memory",
        )
        find_stream_enabled: bool = Field(
            default=True,
            description="启用 send_to_find_stream",
        )
        user_memory_enabled: bool = Field(
            default=True,
            description="启用 send_to_lookup_user_memory",
        )
        user_context_enabled: bool = Field(
            default=True,
            description="启用 send_to_lookup_user_context",
        )

        # ── 容量配额（default/max 配对）────────────────────────────────
        memory_top_n_default: int = Field(
            default=8,
            description="长期记忆默认返回条数",
        )
        memory_top_n_max: int = Field(
            default=20,
            description="长期记忆最大返回条数",
        )
        per_stream_limit_default: int = Field(
            default=20,
            description="每流默认消息数",
        )
        per_stream_limit_max: int = Field(
            default=80,
            description="每流最大消息数",
        )
        streams_default: int = Field(
            default=4,
            description="默认读取流数量",
        )
        streams_max: int = Field(
            default=10,
            description="最大读取流数量",
        )
        chars_per_message_default: int = Field(
            default=300,
            description="默认单条消息长度",
        )
        chars_per_message_max: int = Field(
            default=1000,
            description="最大单条消息长度",
        )

        # ── 查询行为 ────────────────────────────────────────────────────
        candidate_stream_scan_multiplier: int = Field(
            default=5,
            description="按用户消息回溯群聊候选流的扫描倍数",
        )
        around_user_default: bool = Field(
            default=True,
            description="默认围绕目标用户最近发言截取上下文",
        )
        include_timeline_text: bool = Field(
            default=True,
            description="查询结果包含 timeline 文本",
        )
        include_archived_default: bool = Field(
            default=False,
            description="长期记忆默认包含归档",
        )
        include_knowledge_when_query: bool = Field(
            default=True,
            description="有 query 时允许检索知识类记忆",
        )
        include_related: bool = Field(
            default=True,
            description="检索关联记忆",
        )

    @config_section("auto_inject", title="prompt 自动注入", tag="ai")
    class AutoInjectSection(SectionBase):
        """prompt 构建时自动注入跨流上下文。"""

        # ── 主开关与行为 ────────────────────────────────────────────────
        enabled: bool = Field(
            default=True,
            description="启用 send_to_auto_context_inject：合并注入跨流摘要和当前用户近期上下文",
        )
        auto_discover_prompts: bool = Field(
            default=True,
            description="自动识别可注入 prompt",
        )
        include_summary_index: bool = Field(
            default=True,
            description="自动注入时同时合并跨流摘要索引",
        )
        inject_bot_context: bool = Field(
            default=False,
            description="始终注入 bot 自身在其他流的近期发言上下文（范围受 privacy.bot_self_cross_visibility 限制）",
        )
        fallback_to_bot_self: bool = Field(
            default=True,
            description="无发送者时回退以 bot 自身为注入对象，避免事件流场景丢失跨流上下文",
        )

        # ── 容量配额 ────────────────────────────────────────────────────
        summary_stream_limit: int = Field(
            default=6,
            description="自动注入时最多附带多少条跨流摘要",
        )
        per_stream_limit: int = Field(
            default=10,
            description="自动注入每流消息数",
        )
        max_streams: int = Field(
            default=2,
            description="自动注入最大流数量",
        )
        max_chars_per_message: int = Field(
            default=200,
            description="自动注入单条消息长度",
        )
        cooldown_seconds: int = Field(
            default=30,
            description="同一流自动注入冷却秒数",
        )

        # ── Prompt 名单（手动指定）────────────────────────────────────
        target_prompts: list[str] = Field(
            default_factory=list,
            description="手动补充允许 auto_inject 注入的 prompt 名称（为空则自动发现）",
        )
        nfc_prompts: list[str] = Field(
            default_factory=list,
            description="标记为 NFC（Normalized-Frame-Context）结构化格式的 prompt 名称，注入时走结构化分支",
        )

    @config_section("privacy", title="隐私与脱敏", tag="security")
    class PrivacySection(SectionBase):
        """隐私、黑白名单和脱敏配置。"""

        # ── 跨流可见性 ──────────────────────────────────────────────────
        private_bridge_mode: str = Field(
            default="two_way",
            description="私聊互通模式：off/one_way/two_way",
        )
        bot_self_cross_visibility: str = Field(
            default="follow",
            description=(
                "bot 自身发言跨流可见性："
                "follow（跟随 private_bridge_mode）/ "
                "off（关闭，不注入任何其他流）/ "
                "private（仅私聊间互通）/ "
                "group（仅群聊间互通）/ "
                "all（全部流互通）"
            ),
        )
        allowed_chat_scopes: list[str] = Field(
            default_factory=list,
            description="允许的聊天范围",
        )

        # ── 名单（按 chat_type 切换黑白名单模式）────────────────────────
        group_list_type: str = Field(
            default="blacklist",
            description="群聊名单模式：blacklist/whitelist",
        )
        group_list: list[str | int] = Field(
            default_factory=list,
            description="群聊黑/白名单",
        )
        private_list_type: str = Field(
            default="blacklist",
            description="私聊名单模式：blacklist/whitelist",
        )
        private_list: list[str | int] = Field(
            default_factory=list,
            description="私聊黑/白名单",
        )

        # ── 名单（allowlist/blocklist 分开）────────────────────────────
        platform_allowlist: list[str] = Field(
            default_factory=list,
            description="平台白名单",
        )
        platform_blocklist: list[str] = Field(
            default_factory=list,
            description="平台黑名单",
        )
        user_allowlist: list[str] = Field(
            default_factory=list,
            description="用户白名单",
        )
        user_blocklist: list[str] = Field(
            default_factory=list,
            description="用户黑名单",
        )
        group_allowlist: list[str] = Field(
            default_factory=list,
            description="群白名单",
        )
        group_blocklist: list[str] = Field(
            default_factory=list,
            description="群黑名单",
        )

        # ── 字段暴露控制 ────────────────────────────────────────────────
        require_user_identity: bool = Field(
            default=True,
            description="查询用户上下文时要求明确用户身份",
        )
        expose_person_id: bool = Field(
            default=True,
            description="结果中暴露 person_id",
        )
        expose_message_id: bool = Field(
            default=True,
            description="结果中暴露 message_id",
        )
        expose_stream_id: bool = Field(
            default=True,
            description="结果中暴露 stream_id",
        )
        include_person_profile: bool = Field(
            default=True,
            description="返回用户画像字段",
        )

    @config_section("wander", title="串门功能", tag="ai")
    class WanderSection(SectionBase):
        """串门功能配置。"""

        enabled: bool = Field(
            default=False,
            description="是否启用自动串门",
        )
        dry_run: bool = Field(
            default=True,
            description="空跑模式：只记录决策，不真正发送",
        )
        decision_task_name: str = Field(
            default="",
            description="决策模型任务名，留空时使用 model_tasks.sub_actor",
        )

        # ── 一阶段廉价过滤 ────────────────────────────────────────────────
        pre_pass_probability: float = Field(
            default=0.08,
            description="一阶段过滤通过概率",
        )
        global_cooldown_seconds: int = Field(
            default=180,
            description="全局冷却秒数",
        )
        per_target_cooldown_minutes: int = Field(
            default=60,
            description="单目标冷却分钟数",
        )
        max_per_hour: int = Field(
            default=4,
            description="每小时最多串门次数",
        )

        # ── 候选目标筛选 ──────────────────────────────────────────────────
        active_window_minutes: int = Field(
            default=30,
            description="候选目标最近活跃窗口分钟数",
        )
        candidate_top_k: int = Field(
            default=5,
            description="候选目标数量上限",
        )
        context_messages: int = Field(
            default=8,
            description="源流上下文消息条数",
        )
        target_preview_messages: int = Field(
            default=3,
            description="目标预览消息条数",
        )

        # ── 静默时段 ──────────────────────────────────────────────────────
        quiet_hours_start: int = Field(
            default=1,
            description="静默时段起始小时",
        )
        quiet_hours_end: int = Field(
            default=7,
            description="静默时段结束小时",
        )

        # ── 源流范围（监听哪些流的消息） ──────────────────────────────────
        source_scope_mode: str = Field(
            default="blacklist",
            description="源流范围模式",
        )
        source_groups: list[str] = Field(
            default_factory=list,
            description="源流群号列表",
        )
        source_users: list[str] = Field(
            default_factory=list,
            description="源流私聊用户号列表",
        )

        # ── 目标范围（允许串去哪些流） ────────────────────────────────────
        target_scope_mode: str = Field(
            default="whitelist",
            description="目标流范围模式",
        )
        target_groups: list[str] = Field(
            default_factory=list,
            description="目标群号列表",
        )
        target_users: list[str] = Field(
            default_factory=list,
            description="目标私聊用户号列表",
        )
        allow_private_target: bool = Field(
            default=False,
            description="是否允许向私聊串门",
        )

        # ── LLM 调用参数 ──────────────────────────────────────────────────
        decision_temperature: float = Field(
            default=0.2,
            description="决策模型温度",
        )
        decision_max_tokens: int = Field(
            default=300,
            description="决策模型最大输出 token 数",
        )

    @config_section("prompts", title="提示词", tag="ai")
    class PromptsSection(SectionBase):
        """提示词配置。"""

        system_prompt: str = Field(
            default=_DEFAULT_WANDER_SYSTEM,
            description="串门决策 system prompt",
        )

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
