# Goal: 向无聊天流的好友/陌生人发送消息功能

> Started: 20260617_121919

## Progress Log

### 20260617 - 分析阶段完成

**联网查询结果**：
- SnowLuma (OneBot v11 协议桥接): 支持 `send_private_msg` API，接受任意 `user_id`，不要求好友关系
- Neo-MoFox: `send_api.send_text()` 可向任意 `stream_id` 发送；`stream_api.get_or_create_stream()` 可动态创建流

**本地部署确认**：
- SnowLuma: `E:\SnowLuma-v1.9.7-win-x64`，通过 WS `ws://127.0.0.1:8095` 连接 Neo-MoFox
- Neo-MoFox: `E:\Neo-mofox-instance\bot-3693525299\neo-mofox`，插件目录 `plugins/send_to`

**send_to 插件现状分析**：
- `SendToAction`: 已支持直接传 `user_id` 发送，通过 `_resolve_stream_id()` → `ChatStream.generate_stream_id()` 构造 stream_id → `send_text()`
- `send_to_find_stream`: 仅在 `list_summary_records`（有摘要的流）中搜索，找不到无聊天历史的用户
- `send_to_list_users` / `send_to_lookup_users`: 在 `PersonInfo` 表中搜索，覆盖群聊交互过的用户
- `relay_intent`: 通过 `stream_api.get_or_create_stream()` 创建流并注入虚拟消息

**核心问题**：
1. `send_to_find_stream` 只搜有摘要的流 → 找不到从未私聊过的用户
2. 对于完全陌生用户（非好友、无 PersonInfo 记录），LLM 无法获取 user_id
3. `SendToAction` 虽然支持直接传 user_id，但 LLM 不知道如何获取

**解决方案**：增强 `send_to_find_stream`，使其在精确数字 ID 匹配失败时直接构造 stream_id 返回

## Notes & Warnings

- OneBot v11 标准 API 不区分好友/非好友，`send_private_msg` 接受任意 QQ 号
- 实际能否发送取决于 QQ 号隐私设置（是否允许陌生人消息）
- SnowLuma 本地版本 v1.9.7，支持标准 OneBot v11 API
- Neo-MoFox 版本 1.2.0-rc.1
- send_to 插件版本 3.0.3-alpha
 
## Progress Log (20260617_1900) 
 
### 阶段 1：联网调研 (已完成)
- SnowLuma 是 OneBot v11 协议桥接框架，支持 send_private_msg/send_group_msg 等标准 API 
- SnowLuma 配置 ws://127.0.0.1:8095 反连到 Neo-MoFox 8095 端口 
- Neo-MoFox 是插件化 AI Bot 框架，三层架构 kernel->core->app 
- send_api.send_text(stream_id) 通过 MessageSender->Adapter 发送 
- stream_api.get_or_create_stream 可动态创建 ChatStream (无需预先有聊天历史)
 
### 阶段 2：通读 send_to 插件 (已完成) 
 
- SendToAction 通过 _resolve_stream_id 构造 stream_id 后调用 send_text 
- send_to_find_stream 仅在 list_summary_records (有摘要的流) 中查找 
- SendToFindStreamTool 对 64位hex stream_id 有兜底，对纯数字QQ号没有兜底 
- relay.resolve_target_stream 已正确使用 get_or_create_stream，可创建新流
