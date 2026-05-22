# Send To

**跨聊天流消息发送工具** — Neo-MoFox 插件

---

## 概述

send_to 让 LLM 可以主动将消息发送到**其他**聊天流（群聊或私聊），而不仅仅是回复当前会话。

**典型场景**

- 用户在私聊中提到"帮我告诉群里一句话"，LLM 调用 `send_to` 发送到群聊
- 用户在群聊中说"私聊告诉他"，LLM 调用 `send_to` 发送到某人私聊
- LLM 自行判断需要跨流传达信息

**提供的组件**

| 组件类型 | 名称 | 用途 |
|----------|------|------|
| Action | `send_to` | 向其他聊天流发送文本消息 |
| Tool | `send_to_list_groups` | 列出 bot 可见的群聊，辅助定位 group_id |
| Tool | `send_to_lookup_users` | 按昵称/群名片查找用户候选，辅助定位 user_id |
| EventHandler | `send_to_wander` | （可选）监听消息，由 sub_actor 决策是否主动"串门"到其他聊天流 |

---

## 快速上手

### 安装

将 `send_to/` 目录放入 Neo-MoFox 的 `plugins/` 文件夹，无需额外配置即可使用。

### 工作流程

LLM 在对话中判断需要向其他聊天流发送消息时，按以下流程操作：

```
1. 需要发送到群聊但不知道 group_id？
   → 调用 send_to_list_groups 获取群列表

2. 需要发送到私聊但不知道 user_id？
   → 调用 send_to_lookup_users 查找用户

3. 确定目标后，调用 send_to 发送消息
```

---

## 文件结构

```
send_to/
├── __init__.py              # 插件声明
├── manifest.json            # 插件元数据
├── plugin.py                # 插件入口，注册组件
├── config.py                # 插件配置（含串门相关参数与默认提示词）
├── action.py                # send_to Action 实现
├── _resolve.py              # action 与 wander 共用的 group_hint 解析
├── tools.py                 # send_to_list_groups 和 send_to_lookup_users 工具
└── wander.py                # 串门 EventHandler（可选）
```

---

## 组件详细说明

### send_to Action

向其他聊天流发送文本消息。仅用于发送到**非当前会话**，回复当前会话请使用聊天器自身的回复动作。

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_type` | `str` | ✅ | 目标类型：`group`（群聊）或 `private`（私聊） |
| `content` | `str` | ✅ | 要发送的文本内容 |
| `group_id` | `str` | 二选一 | 目标群的原始 ID（优先级高于 group_hint） |
| `group_hint` | `str` | 二选一 | 目标群名（精确/模糊唯一匹配；多命中返回歧义提示） |
| `user_id` | `str` | 二选一 | 目标用户的平台原始 ID |
| `user_hint` | `str` | 二选一 | 目标用户的昵称或群名片（尝试唯一解析） |
| `platform` | `str` | ❌ | 目标平台标识，默认与当前会话相同 |

**目标解析逻辑**

- **群聊**：优先使用 `group_id`；未提供时通过 `group_hint` 在同平台 `ChatStreams` 中精确/模糊匹配
  - 纯数字 hint 直接当作 group_id
  - 先精确匹配群名（大小写不敏感），再包含匹配
  - 唯一命中时自动使用；多命中返回歧义提示
- **私聊**：优先使用 `user_id`；未提供时通过 `user_hint` 借助 `UserQueryHelper` 解析
- 防止向当前会话发送（会提示"请直接回复"）

---

### send_to_list_groups Tool

列出 bot 可见的群聊，供 LLM 选择目标。

**参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `platform` | `str` | `""` | 平台标识，留空使用当前对话平台 |
| `name_keyword` | `str` | `""` | 群名关键词（大小写不敏感包含匹配），留空按活跃度返回全部 |
| `limit` | `int` | `30` | 最多返回群数（1-100） |

**返回示例**

```json
{
  "action": "send_to_list_groups",
  "total": 2,
  "groups": [
    {
      "group_id": "123456",
      "group_name": "测试群",
      "platform": "qq",
      "last_active_time": 1700000000.0
    }
  ],
  "hint": "拿到候选后调用 send_to(target_type='group', group_id=<选中的>) 发送消息。"
}
```

---

### send_to_lookup_users Tool

按昵称/群名片查找用户候选。

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `keyword` | `str` | ✅ | 昵称或群名片关键词 |
| `platform` | `str` | ❌ | 平台标识，留空使用当前对话平台 |
| `limit` | `int` | ❌ | 最多返回用户数（1-50，默认 20） |

**匹配规则**

- 先精确匹配（`nickname` 或 `cardname` 完全一致），再部分匹配
- 候选多且都疑似时，建议先让用户确认选哪位

---

## 使用示例

### 场景1：用户让 LLM 给特定群发消息

```
用户：帮我告诉"摸鱼群"今晚8点有活动

LLM 内部调用流程：
1. send_to(target_type="group", group_hint="摸鱼群", content="今晚8点有活动")
   → 如果群名唯一匹配，直接发送
   → 如果歧义，返回多个候选
2. 歧义时，LLM 可先调用 send_to_list_groups(name_keyword="摸鱼") 查看群列表
3. 确认 group_id 后重新调用 send_to
```

### 场景2：用户让 LLM 给某人私聊发消息

```
用户：帮我私聊告诉小明明天见

LLM 内部调用流程：
1. send_to(target_type="private", user_hint="小明", content="明天见")
   → 如果昵称唯一匹配，直接发送
   → 如果歧义，返回多个候选
2. 歧义时，LLM 可先调用 send_to_lookup_users(keyword="小明") 查看用户列表
3. 确认 user_id 后重新调用 send_to
```

### 场景3：用户直接提供了 ID

```
用户：给群 123456 发一条"你好"

LLM 直接调用：
send_to(target_type="group", group_id="123456", content="你好")
```

---

## 注意事项

- `send_to` 仅用于跨流发送，目标是当前会话时会拒绝并提示直接回复
- `group_hint` 和 `user_hint` 做唯一性匹配，多命中时返回歧义提示需用户确认
- 串门功能 (`send_to_wander`) 默认关闭，启用前请仔细阅读下方"串门模式"章节

---

## 串门模式（可选，默认关闭）

`send_to_wander` 是一个 EventHandler 形态的组件，让 bot **被启发式**地主动跑去其他聊天流发言：bot 在观察到任意消息时，由 sub_actor 模型独立判断"是否要去某个其他群顺嘴搭一句"。

**这不是定时器**——它只在收到消息时被触发，搭不搭话由 LLM 结合源流近期话题与候选目标的近期话题来决定。

### 设计原则：默认让 bot 闭嘴

- 一阶段廉价过滤就把绝大多数消息挡在门外（默认 8% 概率才进 LLM 决策）
- system 提示词被刻意"凹"成内敛社恐人格，**默认行为是 go=false**
- 候选目标必须最近 30 分钟内有活动，且通过单目标冷却（默认 60 分钟）
- 全局冷却（默认 180 秒）+ 每小时上限（默认 4 次）
- 静默时段（默认 1:00–7:00 不串门）
- 跨平台串门一律拒绝，目标必须在候选集合内（防 LLM 编造 stream_id）

### 配置示例

`config/plugins/send_to/config.toml`：

```toml
[wander]
enabled = true
dry_run = true                # 第一次启用建议保持 true，看日志确认效果
decision_model = ""           # 留空走 model_tasks.sub_actor

pre_pass_probability = 0.08   # 一阶段过滤通过概率（凹得很低）
global_cooldown_sec = 180     # 全局冷却（秒）
per_target_cooldown_min = 60  # 单目标冷却（分钟）
max_per_hour = 4              # 每小时最多串门次数
active_window_min = 30        # 候选目标必须最近 N 分钟内活跃
candidate_top_k = 5
context_messages = 8
target_preview_messages = 3

quiet_hours_start = 1         # 静默时段（24h 制，[start, end)）
quiet_hours_end = 7

source_scope_mode = "blacklist"  # 监听哪些源流的消息
source_groups = []
source_users = []

target_scope_mode = "whitelist"  # 允许串去哪些目标（建议白名单）
target_groups = ["123456789"]
target_users = []
allow_private_target = false     # 私聊串门总开关（默认关）

decision_temperature = 0.2
decision_max_tokens = 300
```

### 启用流程建议

1. `enabled = true, dry_run = true` 起步，观察 `[wander]` 日志中决策频率与内容
2. 如果 LLM 偏向 go=true 太多，下调 `pre_pass_probability` 或修改 `prompts.system_prompt` 加强限制
3. 验收通过再 `dry_run = false`，正式启用
4. 调整 `target_groups` 白名单，先在小范围群验证

### 工作流程

```
收到消息 → 同步过滤（静默时段/冷却/概率/源流范围）→ 派发后台任务
        ↓
    加载源流近期上下文 + 候选目标列表（已过滤冷却）
        ↓
    sub_actor 决策（强制 JSON: {"go": bool, "target_stream_id", "content", "why"}）
        ↓
    通过候选集合校验 + 跨平台校验 → 发送（或 dry_run 仅打日志）
        ↓
    更新冷却状态
```

### 日志关键字

- `[wander][DRY_RUN] 决策串门 -> ...`：空跑模式下的决策日志
- `[wander] 串门成功 -> ...`：实际发送
- `[wander] 决策不串门：why=...`：被 LLM 拒绝
- `[wander] LLM 选择的目标 ... 不在候选集合中`：LLM 编造 stream_id 已拦截

---

## 安装

将 `send_to/` 目录放入 Neo-MoFox 的 `plugins/` 文件夹。

**要求**：Neo-MoFox >= 1.0.0 · Python >= 3.11

---

## 许可证

GPL-3.0
