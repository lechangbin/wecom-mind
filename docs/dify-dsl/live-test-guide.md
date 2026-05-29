# Dify 双应用实机测试说明

当前运行链路只保留首批两个 Dify 应用：

- `group_knowledge_reply`：群内 @ 机器人后的知识库答疑。
- `chat_proactive_reminder`：非 @ 消息窗口扫描后的意图触发知识库主动答疑。

旧的 `reply_generation`、`intent_detection`、`conversation_segmentation`、`user_profile_analysis` 不再作为默认运行链路。

## 环境变量

```env
DIFY_CLIENT_MODE=real
DIFY_BASE_URL=http://localhost
DIFY_GROUP_KNOWLEDGE_REPLY_API_KEY=your-group-knowledge-reply-key
DIFY_CHAT_PROACTIVE_REMINDER_API_KEY=your-chat-proactive-reminder-key
DIFY_USER=wecom-bot-system
```

企微智能机器人长连接实机链路需要同时配置：

```env
WECOM_REPLY_AIBOT_ID=
WECOM_REPLY_AIBOT_SECRET=
WECOM_REPLY_AIBOT_NAME=智能机器人

# 主动提醒采集机器人；需要具备群消息读取权限。
WECOM_INTENT_AIBOT_ID=
WECOM_INTENT_AIBOT_SECRET=
WECOM_BOT_ID=
WECOM_BOT_SECRET=

# 历史消息补漏配置。只在测试群确认后开启。
WECOM_MESSAGE_RECONCILE_ENABLED=false
WECOM_MESSAGE_RECONCILE_CHATIDS=
WECOM_MESSAGE_RECONCILE_INTERVAL_SECONDS=10
WECOM_MESSAGE_RECONCILE_LOOKBACK_SECONDS=12
WECOM_MESSAGE_RECONCILE_OVERLAP_SECONDS=2
WECOM_MESSAGE_RECONCILE_PAGES=1
WECOM_MESSAGE_RECONCILE_AUTO_ENQUEUE=true
WECOM_MESSAGE_RECONCILE_AUTO_SEND=false
WECOM_MCP_CONFIG_ENDPOINT=https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config

WECOM_SENDER_MODE=aibot_ws
```

Bot Secret 只放本地 `.env` 或部署环境变量，不写入文档、测试或提交内容。

自动客服实机测试阶段需要在本地 `.env` 中打开：

```env
WECOM_MESSAGE_RECONCILE_ENABLED=true
WECOM_MESSAGE_RECONCILE_AUTO_ENQUEUE=true
WECOM_MESSAGE_RECONCILE_AUTO_SEND=true
WECOM_SENDER_MODE=aibot_ws
```

如果先用群机器人 Webhook 发送，可以改用旧发送通道：

```env
WECOM_SENDER_MODE=webhook
WECOM_GROUP_BOT_WEBHOOK_URL=
```

## @ 答疑测试

1. 一键启动 API 服务和长连接 worker：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-services.ps1
```

2. 在测试群里 @ 回复机器人发送问题。

长连接 worker 会自动完成：

```text
receive frame -> ingest_message -> evaluate_triggers -> Dify -> outbox -> callback-bound aibot_ws reply_stream
```

成功后会生成：

- `messages.source = aibot_ws`
- `ai_runs.workflow_code = group_knowledge_reply`
- `outbox_messages.scene = reply`
- `outbox_messages.status = sent`

仍可用手动接口调试已入库消息：

```http
POST /api/triggers/evaluate
Content-Type: application/json

{
  "message_id": 1
}
```

## 主动答疑手动调试

先确保同一个 `chatid` 已经有消息入库，再调用：

```http
POST /api/proactive-replies/run
Content-Type: application/json

{
  "chatid": "CHAT_ID",
  "time_range": {
    "start": "2026-05-27T00:00:00+08:00",
    "end": "2026-05-27T23:59:59+08:00"
  },
  "auto_enqueue": true
}
```

成功且 Dify 判断需要发送时会生成：

- `ai_runs.workflow_code = chat_proactive_reminder`
- `outbox_messages.scene = proactive`
- `outbox_messages.content.markdown.quote_msgid`
- `outbox_messages.status = pending`

然后调用：

```http
POST /api/outbox-messages/{outbox_id}/send
```

这个接口保留为手动调试入口。实机自动客服链路不依赖手动调用该接口。

## 补漏与主动提醒复测

当前阶段已具备真实 MCP 消息源适配、常驻消息补漏 worker 和可选自动发送。自动客服回答的验收口径如下：

1. 在 `.env` 配置 `WECOM_MESSAGE_RECONCILE_ENABLED=true` 和测试群 `WECOM_MESSAGE_RECONCILE_CHATIDS`。
2. 设置 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true`，并确认 `WECOM_SENDER_MODE=aibot_ws`。
3. 启动 API、长连接 worker 和消息补漏 worker。
4. 在群内发送一条 @ 回复机器人消息，确认实时 @ 回复正常。
5. 等待一次补漏扫描后，确认该消息入库但不会被主动提醒重复处理。
6. 在群内发送一条非 @ 的知识需求消息，等待补漏扫描和主动提醒扫描。
7. 确认补漏拉取的用户消息落库后 `messages.userid` 非空。
8. 确认主动提醒只引用输入中真实存在的 `msgid`，且不引用机器人消息。
9. 确认 Dify 返回 `should_send=true` 时创建 `scene=proactive` outbox，并由长连接 worker 复用 `aibot_ws send_message` 自动发送。
10. 确认 `outbox_messages.status=sent`，或失败时为 `failed` 且保存错误原因。
11. 确认 `wecom_mcp_pull_cursors.last_pulled_at` 在成功扫描后更新。

## 当前注意事项

- `WECOM_MESSAGE_RECONCILE_AUTO_SEND=false` 时，补漏 worker 只创建 pending outbox；自动客服阶段必须在测试群明确开启 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true`，且同时启动长连接 worker。
- `quote_msgid` 已保存到 outbox 内容中，但当前企微发送器还没有真正调用“引用消息”能力。
- 复杂频控、失败重试队列和人工审核策略仍按预排文档留到后续阶段。
