# send_to 插件参考文档（架构与实现）

> 版本：3.0.5
> 本文基于对全部源码的逐行阅读整理，作为后续开发/扩展的参考底稿。
> 约定：下文用 **st** 指代 send_to 插件本身。

> **扩展边界（硬约束）**
> 后续所有"类脑状态系统"相关实现，**只允许修改 send_to 插件目录下的文件**
> （`plugins/send_to/*.py`、`plugins/send_to/manifest.json`、`plugins/send_to/docs/*`、
> `config/plugins/send_to/config.toml`）。
> **严禁修改主程序框架** —— 即 `main.py`、`src/**`、`config/core.toml`、
> `config/model.toml` 等任何非本插件目录的文件。主程序只作为能力提供方被调用。
> 本章给出的所有 API 调用点（`UnifiedScheduler`、`PersonInfo`、`prompt_api`、
> `send_api`、`llm_api`、`database_api`、`storage_api`、`adapter_api`、`EventType`、
> `BaseEventHandler`）均已在本章第 10.7 节逐一验证存在且签名明确，使用前
> 不需要、也不允许去改动它们。

---

## 0. 一句话定位

st 是一个**跨聊天流（cross-stream）中枢插件**：它让 bot 能够「看到别处、记住事情、跑去别处发言、把一段意图转告到别处」。围绕这个定位它实现了五大能力簇：

1. **跨流发送/执行**（dispatch）：主动向其他群/私聊发文本，或在其他流里触发某个 Action。
2. **跨流摘要索引**（index）：为每个聊天流维护一份滚动摘要，注入到人格提示词（system reminder）。
3. **每日短期记忆**（daily_memory）：以 bot 第一人称、按天对群聊做全量/增量回忆归档。
4. **上下文/记忆查询**（lookup + tools）：把「某用户在别处说过什么」「某群最近几天发生了什么」做成 LLM 可调用的工具。
5. **自动注入**（auto_inject）：在 prompt 构建时，自动把「当前说话人在其他流的近期对话」追加进上下文。

外加两个默认关闭的实验能力：**relay 转告**（把一段意图注入目标流让它自然续接）和 **wander 串门**（bot 自发跑去别的群搭话）。

---

## 1. 文件清单与职责

| 文件 | 行数 | 职责 |
|---|---|---|
| `plugin.py` | 125 | 插件入口。`get_components()` 按配置开关决定加载哪些组件；`on_plugin_loaded` 同步 reminder + 启动每日记忆归档后台循环。 |
| `config.py` | 530 | 配置定义，10 个 section（plugin/dispatch/relay/index/daily_memory/lookup/auto_inject/privacy/wander/prompts）。 |
| `action.py` | 671 | 4 个 Action：`SendToAction`（发文本）、`SendToExecuteAction`（跨流执行动作）、`SendToSummaryUpdateAction`（写摘要）、`SendToRelayIntentAction`（转告）。含目标解析（群名/昵称→ID）逻辑。 |
| `tools.py` | 498 | 8 个 LLM 可调用 Tool：列群、列用户、查用户、找流、查流上下文、查每日记忆、查用户记忆、查用户上下文。 |
| `context_lookup.py` | 476 | lookup 后端逻辑：黑白名单校验、脱敏、用户解析、聊天流上下文抓取与格式化。 |
| `stream_index.py` | 650 | 跨流摘要的存储、LLM 滚动更新、actor reminder 同步。 |
| `daily_memory.py` | 786 | 每日短期记忆：状态机、触发条件、全量/增量 LLM 归档、跨天处理、后台守护循环。 |
| `auto_inject.py` | 762 | 监听 `on_prompt_build`，把跨流上下文注入当轮 prompt。 |
| `event_handler.py` | 66 | `SendToAutoSummaryHandler`：监听消息收发事件，派发摘要更新和每日记忆更新两个后台任务。 |
| `wander.py` | 552 | 串门 EventHandler：多重过滤 + sub_actor 决策，决定是否主动跑去别的流发言（默认关）。 |
| `relay.py` | 205 | 转告后端：构造虚拟消息注入目标流并唤醒其 stream loop。 |
| `commands.py` | 112 | 2 个 OWNER 命令：`/send_to_memory`（强制重生成当日记忆）、`/send_to_relay`（调试占位）。 |
| `privacy.py` | 107 | 隐私过滤：消息是否收集、摘要是否注入；私聊互通三档模式（off/one_way/two_way）。 |
| `utils.py` | 136 | 公共工具：配置读取、文本清洗/截断、数值约束、时间格式化、异步锁、黑白名单检查。 |

---

## 2. 组件注册全景（manifest.json ↔ plugin.py）

`get_components()` 根据配置开关动态返回组件。下表为「组件 → 由哪个配置开关控制 → 默认是否启用」：

| 组件 | 类型 | 控制开关 | manifest 默认 |
|---|---|---|---|
| `send_to` | action | `dispatch.enable_send_text` | ✅ 开 |
| `send_to_execute` | action | `dispatch.enable_execute_action` | ❌ 关 |
| `send_to_update_stream_summary` | action | `index.enabled` | ✅ 开 |
| `send_to_relay_intent` | action | `relay.enabled` | ❌ 关 |
| `send_to_list_groups/users/lookup_users` | tool | `dispatch.enable_target_tools` | ✅ 开 |
| `send_to_find_stream` | tool | `lookup.enable_find_stream` | ✅ 开 |
| `send_to_get_stream_context` | tool | `lookup.enable_stream_context` | ✅ 开 |
| `send_to_get_daily_memory` | tool | `lookup.enable_daily_memory` | ✅ 开 |
| `send_to_lookup_user_memory` | tool | `lookup.enable_user_memory` | ✅ 开 |
| `send_to_lookup_user_context` | tool | `lookup.enable_user_context` | ✅ 开 |
| `send_to_auto_summary` | event_handler | `index.enabled` | ✅ 开 |
| `send_to_auto_context_inject` | event_handler | `auto_inject.enabled` | ✅ 开 |
| `send_to_wander` | event_handler | `wander.enabled` | ❌ 关 |
| `send_to_memory` | command | `daily_memory.enabled && enable_command` | ✅ 开 |
| `send_to_relay` | command | `relay.enable_debug_command` | ❌ 关 |

---

## 3. 五大能力簇详解

### 3.1 跨流发送 / 执行（action.py）

**SendToAction（`send_to`）**：LLM 主动向其他流发文本。
- 入参：`target_type`(group/private) + `content` + 定位参数（`group_id`/`group_hint`/`user_id`/`user_hint`/`platform`）。
- 目标解析：`_resolve_group_id`（群名精确/模糊匹配，多命中返回歧义提示）、`_resolve_user_id`（纯数字直接当 QQ 号，否则走 `user_query_helper` 解析）。
- 发送机制（关键，action.py:305-356）：**不走 `send_api.send_text`**，而是构造 `Message` → `MessageSender.send_message` → 从 history 移除该消息 → 改注入目标流的 `unread_messages` → `start_stream_loop`。这样目标流的 bot 才会「感知到并响应」，而不是消息躺在历史里没人理。

**SendToExecuteAction（`send_to_execute`）**：在目标流执行任意插件 Action（如 `nai_artist:action:draw`）。默认关闭。通过 `execute_action` API 调用，构造一个带触发者身份的虚拟 `Message`。

### 3.2 跨流摘要索引（stream_index.py）

**存储键**：
- `summary_{stream_id}` → `StreamSummaryRecord`（每流一份滚动摘要）
- `pending_{stream_id}` → 待摘要消息缓冲

**滚动更新流程**（`collect_message_for_auto_summary`）：
1. 每条收发的消息经隐私过滤后追加进 `pending_{stream_id}`。
2. 当缓冲数 ≥ `auto_summary_batch_size` 时，取一批，连同旧摘要喂给 **utils 模型**（`_generate_updated_summary`），输出「覆盖式新摘要」。
3. system prompt 要求摘要首行标注 `[物理ID: xxx]`（群号/QQ），便于跨流核对。

**reminder 同步**（`sync_actor_reminder`）：把所有流的摘要 + 当前群今日短期记忆，拼成一段文本，通过 `prompt_api.add_system_reminder(bucket="actor", name="跨聊天流上下文摘要", ...)` 写入。注意 bucket 固定用 `actor`，复用主程序 chatter 已有的注入通道。KFC 等自定义 context_manager 的 chatter 注入失败时静默降级。

### 3.3 每日短期记忆（daily_memory.py）

**与摘要的区别**（文件头注释明确）：
- 摘要 = utils 模型，小批次滚动，覆盖任意群/私聊。
- 短期记忆 = **actor 模型**，对当天全部消息做一次性全量回忆，**仅群聊**，第一人称视角。

**存储键**：
- `daily_state_{stream_id}` → `DailyState`（轮次计数、当前日期、上次总结时间）
- `daily_memory_{stream_id}_{YYYY-MM-DD}` → `DailyMemoryRecord`（当日回忆 + 水位线 `last_summarized_ts`）

**触发条件**（任一满足即触发，触发后重置）：
1. bot 完成 N 轮交互（一轮 = 收到 inbound 后 bot 首次 outbound）；
2. 距上次总结超过空闲时间（默认 3 小时）。

**增量 vs 全量**：
- 增量（已有今日记录）：旧回忆 + 新消息 → LLM 融合出「完整一天」的新回忆，按 `last_summarized_ts` 水位线只读新消息。
- 全量（`force_full` 或当日首次）：从当天 0 点重读所有消息。

**跨天处理**：① 事件触发时若发现日期变了，先用 actor 模型重做昨天归档；② 后台守护循环（`run_archive_loop`，60 秒一扫）处理「整天没活动也要归档」的群。

**人设注入**：`_build_persona_prompt()` 从 `core.toml [personality]` 取核心人格/侧面/身份/背景，让回忆带 bot 的语气；`adapter_api.get_bot_info_by_platform` 取 bot 名/ID 做第一人称自称。

### 3.4 上下文 / 记忆查询（tools.py + context_lookup.py）

8 个 Tool 都是 LLM 可调用的只读查询入口。核心是 `lookup_user_context`：
- 给定 platform + user_id/hint，找出该用户出现过的聊天流（私聊+群聊），抓近期消息。
- **消息角色标注**（`message_to_dict`）：`sender_role` ∈ {`target_user`（目标用户）, `bot`（机器人自己）, `other`（其他人）}。这个三分标注是后面注入时防止「张冠李戴」的关键。

### 3.5 自动注入（auto_inject.py）— **本次讨论重点**

**触发**：监听 `on_prompt_build` 事件，在 prompt 构建时介入。
**注入对象**：`_resolve_effective_person_id` 解析出「本轮触发说话的人」的 person_id（群聊优先用触发消息的发送者，不回退到流级占位 person_id）。
**注入内容**：`_resolve_cross_streams` 查这个人在**其他流**的近期对话，按 `target_user/其他群成员/bot` 标注，拼成文本。
**注入通道**（两条，二选一，auto_inject.py:732-755）：
- 有 `context_contributions`（KFC/NFC 结构化通道）→ append 一个 `owner=notice, scope=turn, ttl_turns=1, priority=-100` 的贡献项；
- 否则 → 追加到 `values["extra"]`。**关键约束**：不能往 `params` 顶层加新 key，否则 EventBus 的 `next_params` 签名校验会丢弃整个 handler 的修改。
**注入话术**（`_build_injection_text`）：明确告诉 LLM「这是参考上下文，不是用户新消息也不是系统规则」「只把标注为目标用户的行归因给目标用户」「不要主动引用，除非对方提起」。

> ⚠️ 当前注入的是**「触发说话的那个人」在别处的发言**，注入对象始终是「别人（用户）」，从不包含「bot 自己在别处说了什么」。这正是第 5 节要讨论的缺口。

---

## 4. 数据流与事件时序

```
收到/发出消息
   │
   ├─ EventType.ON_MESSAGE_RECEIVED / ON_MESSAGE_SENT
   │     └─ SendToAutoSummaryHandler.execute()
   │           ├─ sync_actor_reminder()  → 刷新 actor bucket 的跨流摘要 reminder
   │           ├─ [task] collect_message_for_auto_summary()  → 滚动更新 summary_{stream}
   │           └─ [task] register_inbound/bot_message()       → 推进 daily_state，按阈值触发当日归档
   │
prompt 构建时
   │
   └─ on_prompt_build
         └─ SendToAutoContextInjectHandler.execute()
               ├─ 解析触发用户 person_id
               ├─ _resolve_cross_streams()  → 查该用户在其他流的近期对话
               ├─ _build_summary_injection_text()（可选，跨流摘要索引）
               └─ 注入 context_contributions 或 values.extra

后台（plugin 启动后）
   └─ run_archive_loop()  每 60s 扫所有 daily_state，处理跨天/无活动归档
```

**两套注入机制的去重**：`index.inject_summary_reminder` 开启时，摘要走 actor reminder；此时 auto_inject 的 `_build_summary_injection_text` 会直接返回空，避免双重注入（auto_inject.py:451）。

---

## 5. 存储键总览（storage_api JSON，命名空间=plugin_name）

| 键模板 | 内容 | 写入方 |
|---|---|---|
| `summary_{stream_id}` | 单流滚动摘要 `StreamSummaryRecord` | stream_index |
| `pending_{stream_id}` | 待摘要消息缓冲 | stream_index |
| `daily_state_{stream_id}` | 当日计数状态 `DailyState` | daily_memory |
| `daily_memory_{stream_id}_{YYYY-MM-DD}` | 当日短期记忆 `DailyMemoryRecord` | daily_memory |
| `brain_state` | 全局 BrainState 单例 | brain_state |
| `mid_memory_{stream_id}_{YYYY-Www}` | 中期记忆 `MidMemoryRecord` | mid_memory |
| `dream_log_{stream_id}_{YYYY-MM-DD}` | 梦境日志 `DreamLogRecord` | dream |

System reminder（非 storage）：bucket=`actor`, name=`跨聊天流上下文摘要`。
类脑状态系统的状态/印象注入**不开新 reminder bucket**，复用 auto_inject 的
`values.extra` / `context_contributions` 双通道（见 10.5）。

---

## 6. 隐私模型（privacy.py + config.privacy）

- **群黑白名单** / **私聊黑白名单**：`group_list_type`/`group_list`、`private_list_type`/`private_list`。
- **私聊互通三档**（`private_bridge_mode`）：
  - `off`：私聊完全不参与跨流（不收集、不注入）；
  - `one_way`：私聊消息会被收集、摘要正常更新，但摘要**只在该私聊自身**可见（私聊能看群摘要，群看不到私聊）；
  - `two_way`：完全双向互通。
- **脱敏开关**：`expose_person_id` / `expose_message_id` / `expose_stream_id`，关闭时查询结果里对应字段返回 None。
- 两个过滤时机：`should_collect_message`（收集阶段）、`should_show_in_reminder`（注入阶段）。

---

## 7. LLM 模型角色使用

| 用途 | 模型任务（task） | 配置项 |
|---|---|---|
| 跨流摘要滚动更新 | `utils`（默认） | `index.auto_summary_task_name` |
| 每日短期记忆归档 | `actor`（默认） | `daily_memory.task_name` |
| 串门决策 | `sub_actor` | wander 内部 |
| 做梦（梦境联想重组） | `actor`（默认） | `brain.dream_task_name` |
| 主动思考决策 | `sub_actor`（默认） | `brain.active_thinking_task_name` |
| 中期记忆归档 | `actor`（默认） | `brain.mid_memory_task_name` |
| 人物印象更新（聊天后/做梦后） | `actor`（默认） | `brain.impression_task_name` |

取用方式统一：`llm_api.get_model_set_by_task(name)` → `llm_api.create_llm_request(model_set, request_name)` → `request.add_payload(LLMPayload(ROLE.SYSTEM/USER, Text(...)))` → `await request.send(stream=False)`。

---

## 8. 扩展时要注意的坑（从源码里读出来的硬约束）

1. **EventBus `next_params` 签名校验**：`on_prompt_build` 处理器**不能给 params 顶层加新 key**，只能改已有 key（如 `values`/`context_contributions`），否则整个 handler 的修改被丢弃。
2. **跨流发文本必须走 unread + start_stream_loop**，否则目标 bot 不响应（见 3.1）。
3. **群聊 person_id 不能用流级占位值**：群聊里 `ChatStreams.person_id` 可能是占位，必须用本轮触发消息的发送者，否则会把所有注入消息错误归因到一个人。
4. **reminder 注入对某些 chatter（KFC）会失败**：要 try/except 静默降级。
5. **actor bucket 是共享的**：st 复用主程序 chatter 已有的 `actor` reminder bucket，改动注意不要冲掉别的注入。
6. **短期记忆仅群聊**：私聊不做每日记忆归档。

---

## 9. 待实现：Bot 自我跨流上下文注入（目标一）

### 9.1 背景与缺口

当前 `auto_inject` 注入的是「**本轮触发说话那个人**在其他流的近期发言」（见 3.5）。bot 自己在别处说过/做过什么，只是作为时间线里标注 `bot` 的行附带可见，**没有被当成一等主体来组织和强调**。

缺口带来的问题：当主人在 B 流找 bot 说话时，bot **想不起自己刚在 A 流干了什么**，导致：
- 前后行为不一致（在 A 群答应的事，到 B 群忘了）；
- 无法主动 callback / 圆场 / 致歉自己的跨流行为。

**目标一** = 新增一条「以 bot 自己为主体」的跨流上下文注入链路，让 bot 在对话（尤其与主人对话）时能「想起自己最近在别处的言行」。

> 范围声明：本章只解决**事实一致性**（bot 记得自己干了啥）。不承担「强制 bot 遵守主人安全准则」——框架自身有约束，不在本插件处理。

### 9.2 设计要点

| 维度 | 决策 | 理由 |
|---|---|---|
| 注入主体 | bot 自己（`person_id == "bot"` 或 `sender_id == bot_id`） | 与现有"用户主体"注入区分开 |
| 触发时机 | 复用 `on_prompt_build`，与现有注入同一处理器内并行产出 | 不新增 handler，省事件开销 |
| 触发条件 | 默认仅当**当前说话人是主人**时才注入（可配开放给所有人） | 省 token；自我回顾对主人场景最有价值 |
| 主人识别 | 读 `core.toml [permission].owner_list`（格式 `platform:user_id`），与当轮触发者 person_id/sender_id 比对 | 复用框架权限体系，不自造 |
| 数据来源 | 查 `Messages` 表中 `person_id="bot"`（或 sender_id=bot_id）的近期跨流发言，排除当前流 | bot 发言在库里有稳定标识 |
| 注入通道 | 复用现有 `context_contributions` / `values.extra` 双通道 | 不能往 params 顶层加 key（坑#1） |
| 话术视角 | 第一人称："你（bot）最近在其他场合说过/做过这些…" | 与"用户在别处说了什么"的第三人称话术区分 |
| 隐私过滤 | **必须接入** `should_show_in_reminder` / 黑白名单 / 私聊互通三档 | 防止 A 群私密话题被带到 B 群（见 9.5） |
| 冷却 | 复用 `_recent_queries` 冷却字典 | 与现有机制一致 |

### 9.3 改动清单（按文件）

**`config.py` — `AutoInjectSection` 新增字段：**
```
inject_bot_self: bool = False              # 自我注入总开关
bot_self_only_for_owner: bool = True       # 仅主人触发时注入
bot_self_max_streams: int = 3              # 最多回顾几条流
bot_self_per_stream_limit: int = 5         # 每流取几条 bot 发言
bot_self_max_chars_per_message: int = 80   # 单条预览长度
```

**`auto_inject.py` — 新增函数：**
- `_is_owner(person_id, sender_id) -> bool`：读 `owner_list` 比对当轮触发者。
- `_resolve_bot_self_streams(current_stream_id, platform, config) -> list[dict]`：
  - 查 `Messages` 表 `person_id="bot"`（或 sender_id=bot_id）最近发言，按 stream 分组、排除当前流、按活跃度取前 N；
  - 每条流走 `should_show_in_reminder` 过滤；
  - 复用 `_content_preview` / `_format_time` 拼时间线。
- `_build_bot_self_injection_text(bot_streams, is_kfc) -> str`：第一人称话术，明确"这是你自己在别处的近期言行，供你保持前后一致；不要无端复述，除非相关"。

**`auto_inject.py` — `execute()` 内集成：**
在现有 `user_context_text` 之后、`_merge_injection_text` 之前，加：
```
bot_self_text = ""
if config.auto_inject.inject_bot_self:
    if (not config.auto_inject.bot_self_only_for_owner) or _is_owner(person_id, trigger_sender_id):
        bot_streams = await _resolve_bot_self_streams(stream_id, platform, config)
        bot_self_text = _build_bot_self_injection_text(bot_streams, is_kfc=is_kfc)
injection_text = _merge_injection_text(summary_text, user_context_text, bot_self_text)
```
（`_merge_injection_text` 需扩展为可变参数，按非空块用 `\n\n---\n\n` 拼接。）

### 9.4 注入文本样式（建议）

```
## 你最近在其他场合的言行（自我回顾）
以下是你（bot）最近在别的聊天里说过/做过的事，供你保持前后一致；
这不是用户的新消息，也不要无端复述，除非当前话题相关：

【群聊：XX 群】
[14:02] 你: 答应帮 阿喵(123456) 整理一份清单
[14:05] 你: 说今晚之前发出来

【私聊：YY】
[13:30] 你: 跟对方约好明天联机
```

### 9.5 隐私红线（实现时不可省）

自我注入是**新的一条跨流数据外泄路径**：bot 在 A 群对某人的评价、A 群的敏感话题，可能被注入到 B 群暴露。因此：
- 每条候选流必须过 `should_show_in_reminder(config, chat_type, target_id, current_chat_type)`；
- 受 `private_bridge_mode`（off/one_way/two_way）约束：默认 `one_way` 下，私聊的 bot 发言不应注入到群聊场景；
- 受群/私聊黑白名单约束。
- 这套过滤与现有用户注入共用，不另起炉灶。

### 9.6 验证

- 单测：构造 bot 在 A 流的发言 + 主人在 B 流触发，断言注入文本含 A 流 bot 发言、且非主人触发时不注入。
- 隐私：构造 `private_bridge_mode=one_way`，断言私聊 bot 发言不进群聊注入。
- 回归：`inject_bot_self=False`（默认）时行为与现状完全一致。
- 跑现有 `test_auto_inject.py` 确保不破坏既有注入。

---

## 10. 待实现：类脑状态系统（目标二）

### 10.0 设计来源与边界声明

**来源**：本节基于对聊天记录中作者（拾风）提出的功能清单的整理，以及对主程序框架（`src/`）和本插件全部源码的逐行核对。聊天记录原文给出的功能清单是：

> "中短期记忆，睡眠，做梦，忙碌，情绪变化，人物关系（印象），主动思考"

以及一句关键设计哲学：

> "因为我发现这些是联系起来的"

**这意味着这些功能不能做成 8 个互相独立的插件，而应当共享一个中枢状态机 + 一条记忆流，各功能只是它的读写方。**

**核心边界（不可逾越）**：
- ✅ **允许**：在本插件目录内新增模块（如 `brain_state.py`、`sleep.py`、`emotion.py`、`dream.py`、`impression.py`、`active_thinking.py`）、新增配置段、新增 EventHandler、新增 Action、新增 Tool、新增 Command、调用主程序提供的 API、读写本插件命名空间下的 `storage_api` 数据。
- ❌ **禁止**：修改 `src/` 下的任何文件、修改 `core.toml` / `model.toml`、新增主程序 ORM 表、修改 `PersonInfo` 字段定义、改 `UnifiedScheduler` 行为、改 EventBus 签名。
- ❌ **禁止**：用 monkey-patch / import hack 绕道修改主程序运行时行为。所有接入必须走公开 API。
- 主程序已经提供的能力（调度器、印象字段、注入、广播、LLM 调用、存储、查询）已经在 10.7 节验证，本插件直接调用即可，不需要也不能去改它们。

### 10.1 总体架构：为什么这些是"联系起来的"

拾风那句话是钥匙。这些功能本质是**一个共享的「状态机 + 记忆流」，各功能只是它的读写方**。中枢是 `BrainState`，所有状态类字段集中在一个 dataclass 里持久化；所有动作类功能（做梦、主动思考、串门）共享这个中枢，并且互相门控：

```
                       ┌─────────────────────────────────────┐
                       │   BrainState（中枢，全局单例）        │
                       │  energy / mood / busy / sleep_phase  │
                       │  updated_at / last_tick_ts           │
                       └──────┬────────────────────┬──────────┘
              写↑（事件/调度）│                    │读（注入人格）
   ┌─────────────────────────┴──┐      ┌──────────┴──────────────────┐
   │ 输入侧                       │      │ 输出侧（注入到人格 prompt）   │
   │  • ON_MESSAGE_RECEIVED       │      │  • 当前情绪 → system_reminder │
   │  • ON_MESSAGE_SENT           │      │  • 当前作息阶段（困/睡）       │
   │  • UnifiedScheduler tick     │      │  • 当前说话人的印象（PersonInfo）│
   │  • tick 内随时间衰减          │      │  → 影响 LLM 回复风格           │
   └──────────────────────────────┘      └───────────────────────────────┘
                  │
        ┌─────────┴──────────────────────────────────────┐
        │ 记忆流：短期(当天 daily_memory) → 睡眠时整理      │
        │           → 长期(PersonInfo.impression)          │
        │  ↑ 做梦：睡眠阶段对当天短期记忆做 LLM 联想重组     │
        └────────────────────────────────────────────────────┘
                  │
        ┌─────────┴──────────────────────────────────────┐
        │ 动作类（受 sleep/busy/energy 门控）              │
        │  • 做梦 dream（依赖 sleep_phase=sleeping）       │
        │  • 主动思考 active_thinking（受 sleep/energy 门控）│
        │  • 串门 wander（已有，复用门控）                   │
        │  • 记忆整理 consolidate（睡眠时触发）              │
        └────────────────────────────────────────────────────┘
```

**联系点举例**（这些是设计时必须保证的耦合，不是巧合）：

| 触发 | 直接影响 | 间接影响 |
|---|---|---|
| `sleep_phase` 进入 `sleeping` | 能量回升 `energy↑` | 主动思考/串门/wander 全部压制；梦境 `dream` 解锁 |
| `busy↑` 拉高 | `mood.arousal↑`、`mood.valence↓` | 主动思考概率下降；回复更简短 |
| 收到 @bot | `mood.arousal↑`、`busy↑` | 触发对发送者的印象更新 |
| 做梦完成 | 写 `PersonInfo.impression` / `attitude` | 睡醒后 reminder 含新印象 |
| `energy<阈值` | 进入 `sleep_phase=drowsy` | reminder 注入"你很困" |

### 10.2 功能分类

| 状态类（被动演化，持续存在，存在 BrainState 里） | 动作类（主动触发，离散发生，靠调度器/阈值触发） |
|---|---|
| 情绪 `mood`（valence/arousal 二维向量） | 做梦 `dream` |
| 忙碌度 `busy`（0–1 标量） | 主动思考 `active_thinking` |
| 作息/睡眠 `sleep_phase` ∈ {awake/drowsy/sleeping} | 串门 `wander`（已有，复用门控） |
| 能量/精力 `energy`（0–1 标量） | 记忆整理 `consolidate`（睡眠时把短期记忆固化为长期） |
| 人物印象 `impression`（**不在 BrainState 里**，复用 `PersonInfo` 字段，见 10.5） | |

状态类靠「调度器周期 tick + 消息事件」演化；动作类靠「调度器定时 + 状态阈值」触发。

### 10.3 落地清单（按依赖顺序，每阶段可独立交付）

每个阶段都是「先定架构再写代码」，对应拾风"没法 vibe"的自评。

#### 阶段 0：地基 —— BrainState 中枢

**目标**：建立全局唯一的 `BrainState` 单例，提供 `load / save / tick / inject` 四个入口。

**新增文件**：`plugins/send_to/brain_state.py`

**BrainState dataclass 字段**：

```python
@dataclass(slots=True)
class BrainState:
    # 作息
    sleep_phase: str = "awake"          # awake / drowsy / sleeping
    energy: float = 1.0                  # 0.0–1.0，清醒时缓慢下降，睡眠时回升

    # 情绪（二维向量，中性 = (0.0, 0.0)）
    mood_valence: float = 0.0            # -1.0(负面) ~ +1.0(正面)
    mood_arousal: float = 0.0           # 0.0(平静) ~ 1.0(高度兴奋)

    # 忙碌度
    busy: float = 0.0                   # 0.0–1.0，随消息密度上升，随空闲衰减

    # 元数据
    updated_at: str = ""                # ISO8601
    last_tick_ts: float = 0.0           # Unix ts，用于计算 dt
    last_dream_ts: float = 0.0          # 上次做梦时间，门控做梦冷却
    last_active_thinking_ts: float = 0.0
```

**存储键**：`brain_state`（全局单例，bot 只有一个"脑"）
- 通过 `storage_api.save_json(plugin.plugin_name, "brain_state", asdict(state))` 持久化
- 读取：`storage_api.load_json(plugin.plugin_name, "brain_state")`

**核心函数**：

```python
async def load_state(plugin) -> BrainState: ...
async def save_state(plugin, state: BrainState) -> None: ...
async def tick(plugin, now: float) -> BrainState:
    """随时间演化：情绪向中性回归、busy 衰减、energy 随清醒时长下降、
    判断是否进入 drowsy/sleeping。所有演化参数读 config.brain。"""
def build_state_reminder_text(state: BrainState) -> str:
    """把当前状态转成 reminder 文本，供 prompt 注入用。"""
```

**tick 演化规则**（在 `brain_state.py` 内实现，参数全部从 `config.brain` 读）：
- `mood_valence *= decay`（向 0 衰减，decay 如 0.95/tick）
- `mood_arousal *= decay`
- `busy *= busy_decay`
- `energy -= awake_drain_per_sec * dt`（awake 时下降）
- `energy += sleep_recover_per_sec * dt`（sleeping 时回升）
- 当 `energy < drowsy_threshold` 且当前 awake → 切到 drowsy
- 当时间进入 `sleep_window`（如 02:00–08:00）→ 切到 sleeping

**配置（新增 config 段）**：在 `config.py` 加 `BrainSection`（见 10.6）。

**注入入口**：新增 `BrainStateInjectHandler`（监听 `on_prompt_build`），把 `build_state_reminder_text(state)` 通过 `auto_inject` 同款的双通道（`context_contributions` 或 `values.extra`）追加到当前 prompt。**复用现有 `_merge_injection_text` 拼接逻辑，不要新开注入通道。**

**调度器接入**：在 `plugin.py:on_plugin_loaded` 里注册一个 TIME 循环任务：
```python
from src.kernel.scheduler import get_unified_scheduler, TriggerType
scheduler = get_unified_scheduler()
await scheduler.create_schedule(
    callback=_brain_tick_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={"interval_seconds": config.brain.tick_interval_seconds},
    is_recurring=True,
    task_name="send_to_brain_tick",
)
```
（`_brain_tick_callback` 内部调用 `tick(plugin, time.time())` 并 `save_state`。）

#### 阶段 1：作息/睡眠（状态类，最独立，先做）

**依赖**：阶段 0。

**核心规则**：
- 配置睡眠时段 `sleep_window_start` / `sleep_window_end`（24h 制，支持跨午夜，**复用 wander 现有的 `_is_in_quiet_hours` 跨午夜判断逻辑**，wander.py:50-58，不要重写）。
- 进入 `sleep_window` → `sleep_phase = "sleeping"`；`energy` 开始回升。
- `sleep_phase = "sleeping"` 时：
  - `add_system_reminder` 注入「你现在已入睡，回复应极度简短或直接不回」。
  - 压制 wander / active_thinking（在它们的门控里读 `BrainState.sleep_phase`）。
- 睡醒时刻（sleeping → awake 切换）触发一次 `dream`（见阶段 4）。

**注入话术**（建议）：
```
## 你当前的状态
- 作息阶段：困倦/睡眠中
- 能量值：23%
提示：你正在犯困/已入睡。若必须回复，请用极简短句（5 字以内），
或直接不回复。不要主动开启新话题。
```

**复用点**：wander.py 已有 `_is_in_quiet_hours(start, end)`，直接 `from .wander import _is_in_quiet_hours` 调用即可。

#### 阶段 2：情绪 + 忙碌（状态类）

**依赖**：阶段 0。

**EventHandler**：新增 `BrainStateEventHandler`，订阅 `ON_MESSAGE_RECEIVED` + `ON_MESSAGE_SENT`（**仿 wander.py 的派发模式**：execute 同步做廉价更新，重活儿丢后台 task，避免阻塞 EventBus 5s 超时）。

**规则演化**：
- 收到一条 inbound → `busy += inbound_busy_delta`；若被 @ → `arousal += @_arousal_delta`
- bot 发出一条 outbound → `busy += outbound_busy_delta`；`valence` 受本轮内容情绪驱动（进阶用 `sub_actor` 打分，简单版先纯规则）
- `tick` 中 `mood` 向中性回归、`busy` 衰减

**sub_actor 打分（进阶，可选）**：仿 wander.py:236-337 的 `_llm_decide`，让 `sub_actor` 模型对最近 N 条消息输出 JSON：
```json
{"valence": -0.2, "arousal": 0.6, "why": "用户在催稿"}
```
低频触发（如每 10 轮一次），不要每条消息都跑。

**注入**：情绪状态写进 `build_state_reminder_text`，与睡眠状态共用注入通道。

#### 阶段 3：中短期记忆分层（复用现有 daily_memory）

**依赖**：阶段 0（不直接依赖，但共享存储命名空间）。

**现状**：`daily_memory.py` 已是"短期记忆"（按天、群聊、actor 模型全量回忆）。

**新增**：「中期记忆」= 多天 daily_memory 的二次摘要，按周/按 N 天滚动。

**存储键**：
- `mid_memory_{stream_id}_{period}` 其中 `period` 形如 `2026-W26` 或 `2026-06`（具体粒度由配置决定）
- 数据结构仿 `DailyMemoryRecord`，加 `covered_dates: list[str]` 字段

**触发**：用 `UnifiedScheduler.create_schedule(TIME, interval_seconds=...)` 跑低频任务（如每天 04:00 跑一次）。**注意与 daily_memory 的 run_archive_loop 区分**：daily_memory 的守护循环是 60s 一扫，处理跨天；中期记忆是低频（天级）批量回看。

**LLM 调用模式**：完全仿 daily_memory.py:332-439 的 actor 调用模板（`get_model_set_by_task` → `create_llm_request` → `add_payload(SYSTEM/USER)` → `send(stream=False)`），只换 system prompt 和 user prompt。

#### 阶段 4：做梦（动作类，依赖睡眠 + 记忆）

**依赖**：阶段 1（sleep_phase）、阶段 3（短期记忆）。

**触发**：在 sleep → awake 切换时（睡醒瞬间）由 `brain_state.tick` 调用一次 `dream`；或 sleeping 期间由调度器周期触发（如每 2 小时一次）。

**核心函数**：`dream.py::dream(plugin, stream_id, memory_date)`

**LLM 任务**：取当天/近几天 `daily_memory_{stream_id}_{date}`，喂给 actor 模型做**联想式重组**。提示词要求：
- 第一人称视角
- 把零散记忆串成主题
- 提炼对每个出现人物的印象变化
- 输出 JSON：`{"dream_narrative": "...", "impression_updates": [{"person_id": "...", "delta": "..."}]}`

**产物两路**：
1. 写入「梦境日志」`dream_log_{stream_id}_{date}`（可选第二天主动分享，作为 active_thinking 的素材）
2. 调用 `impression.py::update_impression_from_dream`（见 10.5）更新 `PersonInfo.impression` / `attitude` —— 这就是"睡醒能记住梦、对人的印象发生变化"的群友期待。

**门控**：`sleep_phase != "sleeping"` 时拒绝执行；`last_dream_ts` 冷却（如 4 小时一次）。

#### 阶段 5：人物印象/关系（复用主程序字段，禁止新建表）

**依赖**：阶段 4（做梦会更新印象）、阶段 2（情绪受印象影响）。

**关键约束**：主程序 `PersonInfo` 表已存在字段（`src/core/models/sql_alchemy.py:324-412`，已核对）：

| 字段 | 类型 | 用途 |
|---|---|---|
| `impression` | `Text` | Bot 对用户的长期印象（自然语言） |
| `short_impression` | `String(500)` | 简短印象摘要 |
| `points` | `Text` | 用户特征点（JSON） |
| `info_list` | `Text` | 用户信息列表（JSON） |
| `attitude` | `Integer` | 关系态度评分（0–100，默认 50） |
| `interaction_count` | `Integer` | 交互次数 |
| `last_interaction` | `Float` | 最后交互 Unix 时间戳 |

**严禁**新建 `Impression` 表或新增 ORM model。所有读写都走 `database_api`：

```python
from src.core.models.sql_alchemy import PersonInfo
from src.app.plugin_system.api import database_api

# 读取
person = await database_api.get_by(PersonInfo, person_id="qq:123456")

# 更新（先取再改）
person = await database_api.get_by(PersonInfo, person_id="qq:123456")
if person:
    await database_api.update(PersonInfo, person.id, {
        "impression": new_impression_text,
        "attitude": max(0, min(100, person.attitude + delta)),
        "last_interaction": time.time(),
        "interaction_count": (person.interaction_count or 0) + 1,
    })
```

**新增函数**（`plugins/send_to/impression.py`）：
- `update_impression_after_chat(plugin, person_id, recent_messages)`：聊天后由 actor 模型基于互动更新印象。
- `update_impression_from_dream(plugin, person_id, dream_narrative)`：做梦时基于梦境内容更新印象。
- `build_impression_reminder(person) -> str`：把当前说话人的印象拼成 reminder 文本。

**注入**：在 `auto_inject` 的注入流程里追加"你对当前说话人的印象"段。复用现有 `_resolve_effective_person_id`（auto_inject.py:181）拿到说话人 person_id，再查 `PersonInfo`。

#### 阶段 6：主动思考（动作类，最后做，最耗脑）

**依赖**：阶段 0、1、2、3（用 BrainState + 记忆做素材）。

**先定义清楚要哪种**（一闪那句"我的主动思考和这个不太一样"是提醒有多种流派）：
- ❌ 随机发言 = 不好，扰民
- ✅ **基于记忆+情绪的自发话题生成**（推荐）：取近期 `daily_memory` + 当前 `mood` + 当前 `energy`，让 actor 模型生成"我现在想说点什么"，走 `broadcast_text` 或 `send_text` 发到原 stream

**触发**：调度器低频触发（默认 30 分钟一次），多重门控：
- `sleep_phase != "sleeping"`（睡眠时不思考）
- `energy > active_thinking_energy_threshold`（累了不思考）
- `busy < active_thinking_busy_threshold`（忙时不思考）
- 概率门（默认 0.1，避免太频繁）
- 全局冷却（默认 2 小时）+ 单 stream 冷却

**与 wander 的关系**：解耦但共享门控。wander 是"跑去别的流"，active_thinking 是"在当前流主动发声"。冷却字典、`_is_in_quiet_hours` 复用 wander 的，但 `wander.enabled=False` 时 active_thinking 仍可独立工作。

**调用模式**：仿 wander.py:236-337 的 `_llm_decide`，输出 JSON：
```json
{"speak": true, "content": "...", "why": "..."}
```

**发送**：`send_api.send_text(content, stream_id=current_stream_id, platform=platform)`。

### 10.4 联系点的具体实现约束

| 联系点 | 实现方式 | 文件 |
|---|---|---|
| 睡眠 → 做梦触发 | `brain_state.tick` 在 sleep→awake 切换时调用 `dream.dream(...)` | brain_state.py → dream.py |
| 睡眠 → 压制 wander/active_thinking | wander/active_thinking 在门控阶段读 `BrainState.sleep_phase` | wander.py, active_thinking.py |
| 睡眠 → 能量回升 | `tick` 内 `energy += sleep_recover_per_sec * dt` | brain_state.py |
| 忙碌↑ → 情绪烦躁 | `tick` 内若 `busy > threshold` 则 `mood_valence -= delta` | brain_state.py |
| 忙碌↑ → 主动思考概率下降 | active_thinking 门控读 `busy` | active_thinking.py |
| 情绪受人物印象影响 | 注入 reminder 时同时含情绪 + 当前说话人印象 | auto_inject.py（扩展） |
| 做梦 → 更新印象 | `dream` 调用 `impression.update_impression_from_dream` | dream.py → impression.py |
| 做梦 → 写梦境日志 | `dream` 写 `dream_log_*` 键 | dream.py |
| 状态 → 注入人格 | `BrainStateInjectHandler` 监听 on_prompt_build | brain_state.py |

### 10.5 存储键总览（新增）

下表与第 5 节的现有存储键合并使用，全部走 `storage_api`（命名空间 = `plugin.plugin_name = "send_to"`）：

| 键模板 | 内容 | 写入方 | 阶段 |
|---|---|---|---|
| `brain_state` | 全局 BrainState 单例 | brain_state | 0 |
| `mid_memory_{stream_id}_{period}` | 中期记忆 | mid_memory | 3 |
| `dream_log_{stream_id}_{date}` | 梦境日志 | dream | 4 |

`PersonInfo.impression/attitude` 走主数据库（`database_api`），不在 storage_api JSON 里。

System reminder（非 storage，bucket=`actor`）新增：
- name=`bot 当前状态`（情绪/作息/能量）—— 阶段 0/1/2
- name=`当前说话人印象` —— 阶段 5

注意：阶段 0/1/2 的状态注入默认走 `auto_inject` 的 `values.extra` / `context_contributions` 双通道（参考 auto_inject.py:732-755），不要新开 reminder bucket，避免与现有 `actor` bucket 冲突（坑#5）。

### 10.6 配置新增段（config.py）

仿现有 `WanderSection` 的写法（config.py:407-510），在 `SendToConfig` 里新增：

```python
@config_section("brain", title="类脑状态", tag="ai")
class BrainSection(SectionBase):
    """类脑状态系统总配置。"""

    enabled: bool = Field(default=False, description="启用类脑状态系统")
    tick_interval_seconds: int = Field(default=300, description="BrainState 演化 tick 间隔")

    # ── 作息/睡眠 ──
    sleep_window_start: int = Field(default=2, description="睡眠起始小时")
    sleep_window_end: int = Field(default=8, description="睡眠结束小时")
    drowsy_energy_threshold: float = Field(default=0.25, description="低于此能量进入 drowsy")
    awake_drain_per_sec: float = Field(default=0.00001, description="清醒时能量下降速率")
    sleep_recover_per_sec: float = Field(default=0.0002, description="睡眠时能量回升速率")

    # ── 情绪/忙碌 ──
    mood_decay_per_tick: float = Field(default=0.95, description="情绪每 tick 向中性回归系数")
    busy_decay_per_tick: float = Field(default=0.92, description="忙碌度衰减系数")
    inbound_busy_delta: float = Field(default=0.05, description="收到一条消息的忙碌增量")
    at_mention_arousal_delta: float = Field(default=0.2, description="被 @ 的 arousal 增量")

    # ── 注入 ──
    inject_state_reminder: bool = Field(default=True, description="注入当前状态到 prompt")

    # ── 做梦 ──
    dream_enabled: bool = Field(default=True, description="启用做梦")
    dream_cooldown_hours: int = Field(default=4, description="做梦冷却小时数")
    dream_task_name: str = Field(default="actor", description="做梦使用的模型任务名")

    # ── 主动思考 ──
    active_thinking_enabled: bool = Field(default=False, description="启用主动思考")
    active_thinking_interval_minutes: int = Field(default=30, description="主动思考触发间隔")
    active_thinking_probability: float = Field(default=0.1, description="触发时的发声概率")
    active_thinking_energy_threshold: float = Field(default=0.4, description="能量门控阈值")
    active_thinking_busy_threshold: float = Field(default=0.3, description="忙碌门控阈值")
    active_thinking_global_cooldown_hours: int = Field(default=2, description="全局冷却小时数")

# 在 SendToConfig 末尾追加：
brain: BrainSection = Field(default_factory=BrainSection)
```

并在 `plugin.py:get_components()` 里追加：
```python
if config.brain.enabled:
    from .brain_state import BrainStateEventHandler, BrainStateInjectHandler
    components.extend([BrainStateEventHandler, BrainStateInjectHandler])
if config.brain.active_thinking_enabled and config.brain.enabled:
    from .active_thinking import ActiveThinkingEventHandler
    components.append(ActiveThinkingEventHandler)
```

并在 `plugin.py:on_plugin_loaded` 里追加调度器注册（见 10.3 阶段 0）。

### 10.7 关键技术对照表（全部已验证）

下表每一行都已对源码核对，文件路径 + 行号准确。**使用前不需要、也不允许修改主程序。**

| 你要的能力 | 主程序提供的 API | 文件:行 | 验证状态 |
|---|---|---|---|
| 定时 tick / 睡眠 / 做梦触发 | `get_unified_scheduler().create_schedule(callback, TriggerType.TIME, trigger_config={"interval_seconds": N}, is_recurring=True, task_name=...)` | `src/kernel/scheduler/__init__.py:49`, `src/kernel/scheduler/core.py:728` | ✅ 已核对 |
| TIME 触发器配置项 | `{"delay_seconds": N}`（一次性）/ `{"interval_seconds": N, ...}`（循环）/ `{"trigger_at": datetime}` | `src/kernel/scheduler/core.py:386-443` | ✅ 已核对 |
| 自定义条件触发 | `TriggerType.CUSTOM` + `trigger_config={"condition_func": async_callable}` | `src/kernel/scheduler/types.py:18`, `core.py:445-460` | ✅ 已核对 |
| 消息事件订阅 | `EventType.ON_MESSAGE_RECEIVED` / `ON_MESSAGE_SENT` | `src/core/components/types.py:52-53` | ✅ 已核对 |
| prompt 构建事件订阅 | 字符串 `"on_prompt_build"`（不是 EventType 枚举成员，直接以 str 形式放入 `init_subscribe`） | `src/core/prompt/template.py:36`, auto_inject.py:554 | ✅ 已核对 |
| EventHandler 基类 | `BaseEventHandler`，`execute(event_name, params) -> (EventDecision, params)`，类属性 `init_subscribe` / `weight` / `intercept_message` | `src/core/components/base/event_handler.py:17, 89-122` | ✅ 已核对 |
| 状态注入人格（存储） | `prompt_api.add_system_reminder(bucket, name, content, insert_type=FIXED, consume=FOREVER)` —— **注意：签名里没有 priority/ttl_turns/scope，这些是 `context_contributions` 字段，不是 reminder 字段** | `src/app/plugin_system/api/prompt_api.py:160-191` | ✅ 已核对（与清单表述不一致，已修正） |
| 状态注入人格（读取） | `prompt_api.get_system_reminder(bucket, names=None) -> str` | `prompt_api.py:194-208` | ✅ 已核对 |
| LLM 请求构造（带 reminder） | `llm_api.create_llm_request(model_set, request_name, with_reminder="actor")` —— `with_reminder` 参数确实存在，会自动构造带 `ReminderSourceSpec` 的 context_manager | `src/app/plugin_system/api/llm_api.py:50-89` | ✅ 已核对 |
| 模型按任务取 | `llm_api.get_model_set_by_task(name)`（如 `"actor"` / `"sub_actor"` / `"utils"`） | `llm_api.py:141-150` | ✅ 已核对 |
| LLM payload 添加 | `request.add_payload(LLMPayload(ROLE.SYSTEM, Text(...)))` / `LLMPayload(ROLE.USER, Text(...))` | daily_memory.py:412,437; wander.py:309-310 | ✅ 已核对 |
| LLM 发送 | `response = await request.send(stream=False); response.message` | daily_memory.py:439; wander.py:312 | ✅ 已核对 |
| 主动发声（单流） | `send_api.send_text(content, stream_id, platform=None, reply_to=None) -> bool` | `src/app/plugin_system/api/send_api.py:17` | ✅ 已核对 |
| 主动发声（广播） | `send_api.broadcast_text(content, stream_ids, platform=None) -> dict[str, bool]` | `send_api.py:496` | ✅ 已核对 |
| 人物印象字段 | `PersonInfo` 表已有 `impression` / `short_impression` / `points` / `info_list` / `attitude`(0-100,默认50) / `interaction_count` / `last_interaction` | `src/core/models/sql_alchemy.py:324-412` | ✅ 已核对 |
| 人物印象读写 | `database_api.get_by(PersonInfo, person_id=...)` / `database_api.update(PersonInfo, id, {...})` | `src/app/plugin_system/api/database_api.py:30-100` | ✅ 已核对 |
| 持久化状态/记忆（JSON） | `storage_api.save_json(plugin_name, key, dict)` / `load_json(plugin_name, key)` / `list_json(plugin_name)` | `src/app/plugin_system/api/storage_api.py:91-150` | ✅ 已核对 |
| 持久化状态/记忆（结构化） | `storage_api.PluginDatabase("data/send_to/brain.db", [MyModel])` —— 独立 SQLite，**如需复杂查询才用；本类脑系统建议用 JSON 即可** | `storage_api.py:158-328` | ✅ 已核对 |
| 跨流候选目标查询 | `QueryBuilder(ChatStreams).filter(platform=...).order_by("-last_active_time").limit(N).all()` | wander.py:120-127 | ✅ 已核对（仿写） |
| bot 信息（名字/ID） | `adapter_api.get_bot_info_by_platform(platform) -> {"bot_id", "bot_name", ...}` | `src/app/plugin_system/api/adapter_api.py:159-169` | ✅ 已核对 |
| 人设取自 core.toml | `get_core_config().personality.{bot_nickname, personality_core, personality_side, identity, background_story}` | `config/core.toml:194-242`, daily_memory.py:217-253 | ✅ 已核对 |
| 主人列表 | `get_core_config().permissions.owner_list`（格式 `["qq:3786449228"]`） | `config/core.toml:317-320` | ✅ 已核对 |
| 后台任务 | `get_task_manager().create_task(coro, name=..., daemon=True)` | plugin.py:110; daily_memory.py 守护循环 | ✅ 已核对 |

### 10.8 复用现有代码的具体清单

下表是"哪些现有文件可以直接复用 / 哪些函数可以直接 import"，避免重写：

| 要做的事 | 复用的现有代码 | 位置 |
|---|---|---|
| 静默时段判断 | `_is_in_quiet_hours(start, end)` | wander.py:50-58 |
| 黑/白名单过滤 | `check_list_membership(target_id, list_type, id_list)` | utils.py:115-136 |
| 文本截断 | `trim_text(text, max_chars)` | utils.py:45-67 |
| 时间格式化 | `format_time(unix_ts)` | utils.py:89-95 |
| 异步锁管理 | `get_or_create_lock(locks_dict, key)` | utils.py:98-112 |
| 配置读取 | `get_config(plugin) -> SendToConfig` | utils.py:24-30 |
| LLM 调用模板 | 整段 `_generate_full_day_summary` 里 `get_model_set_by_task` → `create_llm_request` → `add_payload` → `send` | daily_memory.py:332-439 |
| sub_actor 决策模板 | `_llm_decide` 整段 | wander.py:236-337 |
| EventHandler 派发模式 | `execute` 同步廉价过滤 + `get_task_manager().create_task` 派发后台 | wander.py:358-398 |
| 双通道注入（context_contributions / values.extra） | `SendToAutoContextInjectHandler.execute` 末尾 | auto_inject.py:732-755 |
| 注入文本合并 | `_merge_injection_text(*parts)` —— **需扩展为可变参数** | auto_inject.py:485-491 |
| 触发用户 person_id 解析 | `_resolve_effective_person_id` | auto_inject.py:181-206 |
| 人设 prompt 构造 | `_build_persona_prompt` | daily_memory.py:217-253 |
| 后台守护循环模式 | `run_archive_loop(plugin, stop_event)` | daily_memory.py:675-693 |
| reminder 同步模式 | `sync_actor_reminder`（仿其结构新增 `sync_brain_state_reminder`） | stream_index.py:601-650 |
| 隐私过滤 | `should_collect_message` / `should_show_in_reminder` | privacy.py:30-107 |

### 10.9 与现有 EventHandler 的并发协调

新增的 EventHandler 会和现有的 `SendToAutoSummaryHandler` / `SendToAutoContextInjectHandler` / `WanderEventHandler` 同时订阅 `ON_MESSAGE_RECEIVED` / `on_prompt_build`。需要注意：

1. **weight 排序**（数值越大越先执行）：
   - `BrainStateEventHandler`（ON_MESSAGE_RECEIVED）：weight=15（先于 wander 的 5，先更新状态再让 wander 看到新状态）
   - `WanderEventHandler`：weight=5（保持不变）
   - `SendToAutoSummaryHandler`：保持原值
   - `BrainStateInjectHandler`（on_prompt_build）：weight=3（晚于 `SendToAutoContextInjectHandler` 的 5，让用户的注入先完成，再追加状态段）
   - `SendToAutoContextInjectHandler`：weight=5（保持不变）

2. **不能向 params 顶层加新 key**（坑#1，已在第 8 节明确）：所有 EventHandler 只能改 `values` / `context_contributions` 等已有 key。

3. **EventBus 5s 超时**：所有 `execute` 内部不能直接做 LLM 调用 / DB 慢查询，必须仿 wander 的模式，把重活儿丢到 `get_task_manager().create_task(...)` 后台执行，`execute` 本身只做廉价同步过滤。

4. **EventDecision 返回**：所有 handler 默认返回 `EventDecision.SUCCESS, params`，不拦截消息流。

### 10.10 落地建议（执行顺序）

拾风自评"这又是一个架构设计影响很大的东西…没法 vibe 的"是对的。建议严格按以下顺序：

1. **先做阶段 0 + 1**（BrainState + 睡眠）—— 最独立、能立刻看到效果、验证架构是否站得住。架构对了再往上堆。
2. **再做阶段 5**（人物印象，复用 PersonInfo）—— 不依赖情绪/做梦，可以独立验证 `database_api` 读写链路。
3. **再做阶段 2**（情绪 + 忙碌）—— 依赖阶段 0 的 BrainState。
4. **再做阶段 3**（中期记忆）—— 独立，但和 daily_memory 共享存储。
5. **最后做阶段 4 + 6**（做梦 + 主动思考）—— 最复杂、依赖前面所有阶段。

每个阶段交付时必须满足：
- 默认 `enabled=False`，开启时不影响现有功能（向后兼容）。
- 跑现有 `test_auto_inject.py` 不破坏。
- 单测覆盖：状态演化 / 注入文本 / 隐私过滤 / LLM 调用 mock。
- 文档同步更新本文第 5、7、10.5 节的存储键 / 模型角色 / 配置字段表。

### 10.11 隐私红线（再次强调）

类脑状态系统会引入新的跨流数据路径（做梦会读多天 daily_memory、主动思考会广播到流、印象更新会写 PersonInfo）。每条新路径都必须：

- 过 `privacy.should_collect_message` / `should_show_in_reminder`
- 受 `private_bridge_mode`（off/one_way/two_way）约束
- 受群/私聊黑白名单约束
- 默认配置保守（`enabled=False`，低概率，长冷却）

特别是**做梦时读多天 daily_memory**：如果某天的 daily_memory 含 A 群私密话题，做梦产物（梦境日志、印象更新）不应反向泄露到 B 群。建议：
- 做梦按 stream 隔离，每个 stream 独立做梦，不跨 stream 拉记忆。
- 印象更新只针对当前 stream 出现过的人。
- 梦境日志默认不主动广播，只在 `active_thinking` 触发时作为可选素材。

### 10.12 验证清单

每个阶段交付时跑以下验证：

- **阶段 0**：单测 `tick` 在不同 dt 下的状态演化数值正确；`load/save` 往返一致；调度器注册成功（用 `list_tasks` 查到 `send_to_brain_tick`）。
- **阶段 1**：构造 `sleep_window` 跨午夜场景，断言 `sleep_phase` 在窗口内为 `sleeping`、窗口外为 `awake`；reminder 文本在睡眠时含"已入睡"。
- **阶段 2**：构造 10 条 inbound 消息，断言 `busy` 上升、`mood_arousal` 上升；停止消息后 `tick` 多次后回落到中性。
- **阶段 3**：构造 3 天 daily_memory，跑中期记忆，断言 `mid_memory_*` 键生成、内容含 3 天的关键信息。
- **阶段 4**：mock LLM 返回固定 JSON，断言 `dream_log_*` 写入、`PersonInfo.impression` 被更新。
- **阶段 5**：mock `database_api.get_by` 返回 PersonInfo，断言 reminder 文本含印象；隐私模式 one_way 下断言私聊印象不进群聊 reminder。
- **阶段 6**：mock LLM 返回 `{"speak": true, "content": "..."}`，断言 `send_api.send_text` 被调用；sleep_phase=sleeping 时断言不触发。
- **回归**：跑 `test_auto_inject.py` 全绿；`brain.enabled=False` 时所有现有行为零变化。

---

## 11. 实现落地状态（3.0.5 已交付）

第 10 节定义的七个阶段已全部落地，默认 `brain.enabled=False`，开启时不影响现有功能。

| 阶段 | 模块文件 | 关键函数/类 | 状态 |
|---|---|---|---|
| 0 BrainState 中枢 | `brain_state.py` | `BrainState` / `load_state` / `save_state` / `tick` / `build_state_reminder_text` / `BrainStateInjectHandler` | ✅ |
| 1 作息/睡眠 | `brain_state.py` | `_resolve_sleep_phase`（复用 `wander._is_in_quiet_hours`） / 睡醒触发 `dream.maybe_dream_on_wake` | ✅ |
| 2 情绪+忙碌 | `brain_state.py` | `BrainStateEventHandler`（weight=15）订阅消息事件；tick 内衰减回归中性；busy 超阈值压制 valence | ✅ |
| 3 中期记忆 | `mid_memory.py` | `archive_mid_memory_for_all` / `MidMemoryRecord` / ISO 周标识 `YYYY-Www` / 调度器每 24h | ✅ |
| 4 做梦 | `dream.py` | `maybe_dream_on_wake` / `dream_periodic_during_sleep` / `DreamLogRecord` / 写 `dream_log_*` + 调 `impression.update_impression_from_dream` | ✅ |
| 5 人物印象 | `impression.py` | `update_impression_after_chat` / `update_impression_from_dream` / `build_impression_reminder`（复用 `PersonInfo` 字段，禁新建表） | ✅ |
| 6 主动思考 | `active_thinking.py` | `ActiveThinkingEventHandler`（weight=4）/ 多重门控（sleep/energy/busy/概率/冷却）/ `sub_actor` JSON 决策 / `send_api.send_text` | ✅ |

### 11.1 调度任务（plugin.py `_register_brain_schedules`）

启用 `brain.enabled` 后注册三个 `UnifiedScheduler` TIME 循环任务：

| task_name | 间隔 | 回调 |
|---|---|---|
| `send_to_brain_tick` | `brain.tick_interval_seconds`（默认 300s） | `brain_state.tick` |
| `send_to_mid_memory_archive` | 86400s（24h） | `mid_memory.archive_mid_memory_for_all` |
| `send_to_dream_periodic` | 7200s（2h，受 `dream_cooldown_hours` 门控） | `dream.dream_periodic_during_sleep` |

### 11.2 EventHandler 并发协调（实现 10.9）

| EventHandler | 事件 | weight |
|---|---|---|
| `BrainStateEventHandler` | ON_MESSAGE_RECEIVED / SENT | 15（先于 wander） |
| `WanderEventHandler` | ON_MESSAGE_RECEIVED | 5 |
| `SendToAutoSummaryHandler` | ON_MESSAGE_RECEIVED / SENT | 0 |
| `SendToAutoContextInjectHandler` | on_prompt_build | 5 |
| `BrainStateInjectHandler` | on_prompt_build | 3（晚于 auto_inject） |
| `ActiveThinkingEventHandler` | ON_MESSAGE_RECEIVED | 4 |

所有 handler 均遵守：① 不向 params 顶层加新 key；② 重活儿丢 `get_task_manager().create_task` 后台；③ 默认返回 `EventDecision.SUCCESS`。

### 11.3 注入合并（auto_inject.py）

`_merge_injection_text` 已扩展为可变参数，按非空块用 `\n\n---\n\n` 拼接。
`SendToAutoContextInjectHandler.execute` 现在合并四段：

1. 跨流摘要索引（`_build_summary_injection_text`）
2. 目标用户在其他流的近期对话（`_build_injection_text`）
3. 当前说话人印象（`impression.build_impression_reminder`，受 `private_bridge_mode` 约束）
4. （由 `BrainStateInjectHandler` 独立追加）当前类脑状态

### 11.4 隐私红线落实

- 印象注入走 `should_show_in_reminder`，one_way 私聊互通模式下私聊印象不进群聊 reminder
- 做梦按 stream 隔离，每个 stream 独立做梦，不跨 stream 拉记忆
- 印象更新只针对做梦素材里真实出现过的 person_id
- 梦境日志默认不主动广播，只作为 `active_thinking` 的可选素材
- 主动思考受 `_is_in_quiet_hours` 静默时段 + 全局/单 stream 双冷却压制

### 11.5 配置开关（config.py `BrainSection`）

所有功能默认关闭，启用顺序建议：

```toml
[brain]
enabled = true                  # 总开关
inject_state_reminder = true    # 阶段 0/1/2 注入
impression_enabled = true       # 阶段 5
mid_memory_enabled = true       # 阶段 3
dream_enabled = true            # 阶段 4
active_thinking_enabled = false # 阶段 6（最扰民，最后开）
```

