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
├── action.py                # send_to Action 实现
└── tools.py                 # send_to_list_groups 和 send_to_lookup_users 工具
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
- 无额外配置文件，插件开箱即用

---

## 安装

将 `send_to/` 目录放入 Neo-MoFox 的 `plugins/` 文件夹。

**要求**：Neo-MoFox >= 1.0.0 · Python >= 3.11

---

## 许可证

GPL-3.0
