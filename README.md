# Send To

Neo-MoFox 跨聊天流能力插件。

`send_to` 整合了原 `send_to`、`context_bridge_tool`、`cross_stream_relay` 的主要能力，提供跨流发送、目标查找、上下文查询、长期记忆查询、自动摘要、每日短期记忆、自动注入和可选 relay 转告。

## 功能概览

### 跨流发送

| 组件 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `send_to` | Action | 开启 | 向其他群聊/私聊发送文本 |
| `send_to_execute` | Action | 关闭 | 在其他聊天流执行指定 Action |
| `send_to_list_groups` | Tool | 开启 | 列出 bot 可见群聊 |
| `send_to_list_users` | Tool | 开启 | 列出 bot 已知用户 |
| `send_to_lookup_users` | Tool | 开启 | 按昵称/群名片查找用户 |

### 跨流上下文与记忆

| 组件 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `send_to_find_stream` | Tool | 开启 | 根据名称、索引、QQ、群号、stream_id 查目标流 |
| `send_to_get_stream_context` | Tool | 开启 | 查询其他聊天流的原始消息和摘要 |
| `send_to_lookup_user_context` | Tool | 开启 | 按用户查询相关私聊/群聊近期上下文 |
| `send_to_lookup_user_memory` | Tool | 开启 | 查询用户相关长期记忆（依赖 booku_memory 时可用） |
| `send_to_get_daily_memory` | Tool | 开启 | 查询群聊每日短期记忆 |

### 自动能力

| 组件 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `send_to_auto_summary` | EventHandler | 开启 | 自动维护跨流摘要和每日短期记忆 |
| `send_to_auto_context_inject` | EventHandler | 开启 | prompt 构建时合并注入跨流摘要和用户近期上下文 |
| `send_to_wander` | EventHandler | 关闭 | 由 sub_actor 决策是否主动串门，默认 dry-run |

### Relay / 意识迁移

| 组件 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `send_to_relay_intent` | Action | 关闭 | 将开场白、来源上下文、内部提示注入目标流并冷启动 |
| `send_to_memory` | Command | 开启 | 强制生成本群当日短期记忆，兼容 `/短期记忆`、`/short_memory` |
| `send_to_relay` | Command | 关闭 | relay 调试命令，占位性质 |

## 典型使用方式

### 直接跨流发送

用户说“帮我告诉摸鱼群今晚八点活动”，模型可直接调用：

```text
send_to(target_type="group", group_hint="摸鱼群", content="今晚八点有活动")
```

如果群名或用户昵称有歧义，可先调用：

```text
send_to_list_groups(name_keyword="摸鱼")
send_to_lookup_users(keyword="小明")
```

### 查询其他聊天流

```text
send_to_find_stream(identifier="摸鱼群")
send_to_get_stream_context(stream_identifier="摸鱼群", message_count=30)
```

### 按用户查询上下文和记忆

```text
send_to_lookup_user_context(platform="qq", user_hint="小明", chat_scope="all")
send_to_lookup_user_memory(platform="qq", user_hint="小明", query="最近提到的计划")
```

### 查询短期记忆

```text
send_to_get_daily_memory(stream_identifier="摸鱼群", days=3)
```

主人也可以在群里使用：

```text
/短期记忆
/send_to_memory
/short_memory
```

### Relay 转告

`send_to_relay_intent` 默认关闭。开启后，它不会直接“发一句话”，而是向目标流注入一条带上下文的虚拟消息，让目标流的 bot 自己续接。

建议流程：

```text
send_to_find_stream(identifier="小明", chat_type_hint="private")
send_to_relay_intent(
  target_stream_id="...",
  relay_content="刚才在群里聊到那个配置问题，我过来继续跟你确认一下。",
  opening_hint="目标是确认配置细节，语气保持自然，不要机械复述。"
)
```

## 自动注入说明

`send_to_auto_context_inject` 默认开启。它会在合适的 prompt 构建事件中自动注入：

1. **跨流摘要索引**
   - 来自 `send_to_auto_summary` 自动维护的摘要。
   - 适合提供全局背景。

2. **当前触发用户的其他流近期上下文**
   - 按当前用户查找其相关私聊/群聊消息。
   - 会标注 `目标用户 / 其他群成员 / bot`，避免把别人说的话误认为目标用户说的。

3. **bot 自我回顾**（仅群聊无发送者时触发）
   - 当本轮没有「消息发送者」（事件流、定时触发、外部系统回灌等）且 `auto_inject.fallback_to_bot_self = true` 时，改以 bot 自身为注入对象。
   - 按 `sender_id == bot_id` 查询 bot 自己近期在其他流的发言，注入文本头部明确标注「这是你自己过去说过的话，不是用户新消息」，供 LLM 保持跨流语境连贯、避免自相矛盾。
   - 受 `privacy.bot_self_cross_visibility` 约束。

4. **bot 跨流上下文**（`inject_bot_context = true` 时始终生效）
   - 与用户上下文并列，每次注入都额外追加 bot 自身在其他流的近期发言。
   - 区别于上面的「自我回顾」：自我回顾仅在无发送者时回退触发，本选项无论是否有发送者都会注入。
   - 范围同样受 `privacy.bot_self_cross_visibility` 约束。

相关配置：

```toml
[auto_inject]
enabled = true
include_summary_index = true
summary_stream_limit = 6
max_streams = 2
per_stream_limit = 10
max_chars_per_message = 200
cooldown_seconds = 30
inject_bot_context = false   # 始终注入 bot 自身跨流上下文
fallback_to_bot_self = true  # 群聊无发送者时回退以 bot 自身为注入对象
```

如果 prompt 过长，优先调小：

- `summary_stream_limit`
- `max_streams`
- `per_stream_limit`
- `max_chars_per_message`

## 主要配置分区

| 分区 | 说明 |
|---|---|
| `[plugin]` | 插件总开关和版本 |
| `[dispatch]` | 跨流发送、跨流执行、目标工具开关 |
| `[relay]` | relay 转告能力和调试命令 |
| `[index]` | 跨流摘要、summary reminder、摘要长度和批次 |
| `[daily_memory]` | 每日短期记忆、跨天归档、强制生成命令 |
| `[lookup]` | 上下文查询和长期记忆查询限制 |
| `[auto_inject]` | 自动跨流注入 |
| `[privacy]` | 私聊互通模式、黑白名单、脱敏 |
| `[wander]` | 自动串门，默认关闭 |
| `[prompts]` | 串门决策提示词 |

## 默认关闭的能力

这些能力已保留，但默认关闭：

- `send_to_execute`
  - 可跨流执行任意 Action，能力较强，需谨慎开启。

- `send_to_relay_intent`
  - 会注入目标流并冷启动目标流，适合明确的跨流续接场景。

- `send_to_wander`
  - 自动串门，容易扰民；默认关闭且 dry-run。

- `send_to_relay`
  - 调试命令，当前主要用于说明状态。


## 隐私与互通

`[privacy]` 可控制哪些流参与摘要、注入和查询：

```toml
[privacy]
private_bridge_mode = "two_way"  # off / one_way / two_way
bot_self_cross_visibility = "follow"  # follow / off / private / group / all
group_list_type = "blacklist"
group_list = []
private_list_type = "blacklist"
private_list = []
```

私聊互通模式：

- `off`：私聊完全不参与跨流摘要。
- `one_way`：私聊可看群聊摘要，但私聊摘要不暴露给群聊或其他私聊。
- `two_way`：私聊与群聊双向互通。

bot 自身发言跨流可见性（`bot_self_cross_visibility`）：

- `follow`：跟随 `private_bridge_mode`（默认，向后兼容）。
- `off`：关闭，bot 发言摘要不注入任何其他流。
- `private`：仅私聊间互通（私聊→私聊可见，不进群聊）。
- `group`：仅群聊间互通（群聊→群聊可见，不进私聊）。
- `all`：全部流互通，无限制。

该档位同时作用于 `auto_inject` 的 bot 自我回顾路径：群聊上下文下若档位为 `off` / `private_only`，则跳过 bot 自身跨流发言查询，仅注入跨流摘要段。

## 旧插件迁移

新版 `send_to` 已覆盖：

- `context_bridge_tool`
- `cross_stream_relay`

旧组件名已替换为：

| 旧组件 | 新组件 |
|---|---|
| `context_memory_lookup` | `send_to_lookup_user_memory` |
| `context_stream_lookup` | `send_to_lookup_user_context` |
| `cross_stream_auto_injector` | `send_to_auto_context_inject` |
| `get_stream_raw_context` | `send_to_get_stream_context` |
| `get_daily_memory` | `send_to_get_daily_memory` |
| `find_target_stream` | `send_to_find_stream` |
| `update_stream_summary` | `send_to_update_stream_summary` |
| `relay_to_stream` | `send_to_relay_intent` |

建议先禁用旧插件运行一段时间，确认无旧 prompt 还引用旧工具名后再删除。

注意：旧 `cross_stream_relay` 的摘要和短期记忆数据存储在旧插件命名空间下，`send_to` 不会自动迁移旧数据。如需保留旧数据，需要单独迁移。

## 文件结构

```text
send_to/
├── __init__.py
├── manifest.json
├── plugin.py              # 插件入口和动态组件注册
├── config.py              # 统一配置（10 段）
├── action.py              # send_to / execute / summary update / relay intent
├── tools.py               # 目标查找、上下文查询、记忆查询工具
├── event_handler.py       # 自动摘要和每日记忆事件处理
├── auto_inject.py         # 自动跨流注入
├── stream_index.py        # 跨流摘要存储和 reminder 同步
├── daily_memory.py        # 每日短期记忆
├── context_lookup.py      # 用户上下文与长期记忆查询逻辑
├── privacy.py             # 隐私和黑白名单
├── relay.py               # relay 虚拟消息注入
├── commands.py            # 短期记忆和 relay 调试命令
├── wander.py              # 自动串门
├── utils.py               # 工具函数
└── docs/
    └── ARCHITECTURE_REFERENCE.md  # 架构参考文档
```

## 验证

当前已验证：

```bash
python -m compileall plugins/send_to
python -c "from plugins.send_to.plugin import SendToPlugin; print(SendToPlugin.plugin_name, SendToPlugin.plugin_version)"
```

默认配置下会注册 13 个组件。类脑状态系统已拆分到独立插件 `brian_stats`。

## 要求

- Neo-MoFox >= 1.0.0
- Python >= 3.11

## 许可证

GPL-3.0
