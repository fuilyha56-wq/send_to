# Goal: 跨流发送到无聊天流用户的功能调研与实现

> Started: 20260618_015528

## 调研结论

### SnowLuma (OneBot v11 协议桥)
- TypeScript 框架，桥接 NTQQ 与 OneBot v11
- 提供 `send_private_msg(user_id, message)` API — 可向任意 QQ 号发送私聊消息
- **不需要"创建临时会话"** — OneBot 直接发送即可，QQ 协议层自动处理
- 本地部署：`E:\SnowLuma-v1.9.7-win-x64`，配置在 `config/onebot_*.json`

### Neo-MoFox 框架
- 插件驱动 AI Bot 框架，三层架构
- `stream_api.get_or_create_stream()` 可从 platform+user_id 创建聊天流
- `send_api.send_text()` → `_send_message()` → `MessageSender` → OneBot Adapter
- **关键问题**：`_send_message()` 需要从 stream_info 解析 `target_user_id` 放入 `Message.extra`，若流记录不存在则无法解析 → OneBot 适配器无法确定目标 → 发送失败

### send_to 插件现状
- `SendToAction._resolve_stream_id()` 生成 SHA256 stream_id，但不确保流记录存在
- `SendToFindStreamTool` 仅搜索 summary_records，无历史记录的用户不可见
- `relay_intent()` 已使用 `get_or_create_stream()` — 证明方案可行

### 根因
发送私聊消息前，`ChatStreams` 表中必须有该流的记录，否则 `_send_message()` → `message_to_envelope()` 无法将 `target_user_id` 注入 `Message.extra`，导致 OneBot 适配器无法确定私聊目标。

### 修复策略
1. `SendToAction` / `SendToExecuteAction` 在解析 stream_id 后，调用 `stream_api.get_or_create_stream()` 确保流记录存在
2. `SendToFindStreamTool` 增加纯数字标识符的回退路径，支持按 QQ 号/群号直接查找

## Progress Log

### 20260618_0200 - 调研完成
- 通读 SnowLuma 文档和 OneBot v11 API
- 通读 Neo-MoFox send_api / stream_api / message_sender / converter / onebot_adapter
- 确认根因：流记录缺失导致 target_user_id 无法注入
- 确认修复可行：get_or_create_stream 已存在且被 relay 使用

## Notes & Warnings
- 修改 `_resolve_stream_id` 返回值会影响 2 个调用方（SendToAction、SendToExecuteAction），均在 action.py 内
- `get_or_create_stream` 是幂等的，重复调用安全
- OneBot 发送私聊消息不需要好友关系，但可能被 QQ 平台限制
- 自动创建的流初始无摘要，随消息积累自动生成
