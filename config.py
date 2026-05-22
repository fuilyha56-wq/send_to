"""send_to 插件配置。

配置文件默认路径：config/plugins/send_to/config.toml

包含两段：
- wander：串门功能的开关、冷却、范围、概率等参数
- prompts：sub_actor 决策用的系统提示词（默认值已经"凹"过，倾向于让 bot 闭嘴）
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
    """send_to 插件配置模型。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "send_to 插件配置（含跨流串门）"

    @config_section("wander")
    class WanderSection(SectionBase):
        """串门功能配置。"""

        enabled: bool = Field(
            default=False,
            description="是否启用串门功能（默认关闭，开启前请认真阅读各项参数）",
        )
        dry_run: bool = Field(
            default=True,
            description="空跑模式：只在日志输出 LLM 决策，不真正发送消息。第一次启用建议保持 true",
        )
        decision_model: str = Field(
            default="",
            description="决策模型名（对应 config/model.toml 的 name）。留空时使用 model_tasks.sub_actor",
        )

        # ── 一阶段廉价过滤 ────────────────────────────────────────────────
        pre_pass_probability: float = Field(
            default=0.08,
            description="一阶段过滤通过概率（0-1）。仅这部分消息会进入 LLM 决策。默认 0.08，凹得很低",
        )
        global_cooldown_sec: int = Field(
            default=180,
            description="全局冷却（秒）：bot 任意一次串门后，至少间隔多久才能再串。默认 180s",
        )
        per_target_cooldown_min: int = Field(
            default=60,
            description="单目标冷却（分钟）：同一个目标流多久能再被串门。默认 60 分钟",
        )
        max_per_hour: int = Field(
            default=4,
            description="每小时最多串门次数（防极端情况）",
        )

        # ── 候选目标筛选 ──────────────────────────────────────────────────
        active_window_min: int = Field(
            default=30,
            description="候选目标必须在最近多少分钟内有过消息（避免对死群发言）",
        )
        candidate_top_k: int = Field(
            default=5,
            description="给 LLM 看的候选目标数量上限",
        )
        context_messages: int = Field(
            default=8,
            description="决策时拉取源流的最近多少条消息作为上下文",
        )
        target_preview_messages: int = Field(
            default=3,
            description="每个候选目标向 LLM 展示的最近消息条数",
        )

        # ── 静默时段 ──────────────────────────────────────────────────────
        quiet_hours_start: int = Field(
            default=1,
            description="静默时段起始小时（24 小时制，含）。在 [start, end) 期间不串门",
        )
        quiet_hours_end: int = Field(
            default=7,
            description="静默时段结束小时（24 小时制，不含）",
        )

        # ── 源流范围（监听哪些流的消息） ──────────────────────────────────
        source_scope_mode: str = Field(
            default="blacklist",
            description='源流范围模式："whitelist" 或 "blacklist"',
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
            description='目标流范围模式："whitelist" 或 "blacklist"。建议保持白名单',
        )
        target_groups: list[str] = Field(
            default_factory=list,
            description="允许/禁止串门的群号列表",
        )
        target_users: list[str] = Field(
            default_factory=list,
            description="允许/禁止串门的私聊用户号列表",
        )
        allow_private_target: bool = Field(
            default=False,
            description="是否允许向私聊串门（默认关，私聊主动发言容易骚扰）",
        )

        # ── LLM 调用参数 ──────────────────────────────────────────────────
        decision_temperature: float = Field(
            default=0.2,
            description="决策模型温度（低温度更保守、更接近规则化输出）",
        )
        decision_max_tokens: int = Field(
            default=300,
            description="决策模型最大输出 token 数",
        )

    @config_section("prompts")
    class PromptsSection(SectionBase):
        """决策提示词配置（默认值已经凹好，控刷屏）。"""

        system_prompt: str = Field(
            default=_DEFAULT_WANDER_SYSTEM,
            description="串门决策模块的 system prompt，默认值倾向于让 bot 沉默",
        )

    wander: WanderSection = Field(default_factory=WanderSection)
    prompts: PromptsSection = Field(default_factory=PromptsSection)
